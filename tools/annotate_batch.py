"""Batch mode for tools/annotate.py: the same annotation, through the Message Batches API at half price.

    python tools/annotate.py --batch <ids...> | --ids-file F  --out build/rollout/<run> [--wave 150]

Laws are processed in waves. Each wave runs in rounds, each one batch (or a few, if the batch would exceed the
size limit):
1. every group call for every law in the wave (long texts: one call per part, as in sync mode);
2. calls whose answer hit the output limit, re-split into halves (at most two more rounds);
3. quote repairs for the assembled annotations.
Then each annotation is validated and written, as in sync mode.

**Budget.** Before a batch is submitted, its worst case (every request writing its full prompt to cache and its
full max_tokens of output, at batch prices) is reserved with tools/budget.py; the batch is refused if that could
pass the stop line. Actual cost is recorded from each result's usage. So the cap holds even if every answer runs
to the limit.

**Resume.** Batch IDs are saved in <out>/_batches/w<hash of the wave's ids>-<round>.json before polling. If the process stops,
rerun the same command: a saved batch is collected instead of resubmitted; laws already written are skipped.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from annotate import (CHUNK_TOKENS, MODES, PRICES, TEXTS, Annotator, Truncated, apply_fixes, bad_quotes, combine,
                      cost, est_tokens, model_schema, outline, repair_request, split_text)
from budget import guard, record, release
from quote_check import Doc

MAX_BATCH_BYTES = 150_000_000  # API limit 256 MB
MAX_BATCH_REQUESTS = 20_000    # API limit 100,000
POLL = 60


class BatchRun:
    def __init__(self, ann: Annotator):
        self.ann, self.out = ann, ann.out
        self.state = self.out / "_batches"
        self.state.mkdir(parents=True, exist_ok=True)

    # ---- requests -------------------------------------------------------------------------------------------
    def params(self, system: list, prompt: str, schema: dict, max_tokens: int, effort: str | None) -> dict:
        config = {"format": {"type": "json_schema", "schema": schema}}
        if effort or self.ann.effort:
            config["effort"] = effort or self.ann.effort
        return {"model": self.ann.model, "max_tokens": max_tokens, "system": system, "output_config": config,
                "messages": [{"role": "user", "content": prompt}]}

    def worst_case(self, p: dict) -> float:
        n_in = est_tokens("".join(b["text"] for b in p["system"]) + p["messages"][0]["content"]
                          + json.dumps(p["output_config"]))
        (pin, pout) = PRICES[self.ann.model][1 if n_in > 100_000 else 0]
        return 0.5 * (n_in * pin * 1.25 + p["max_tokens"] * pout) / 1e6

    # ---- one round: submit (or resume), wait, collect ---------------------------------------------------------
    def run(self, key: str, jobs: dict[str, dict], logs: dict[str, dict]) -> dict[str, object]:
        """jobs: custom_id -> {"iid", "params", ...}. Returns custom_id -> parsed output, or an Exception."""
        if not jobs:
            return {}
        ids, batches, size, cur = list(jobs), [], 0, []
        for cid in ids:
            b = len(json.dumps(jobs[cid]["params"]))
            if cur and (size + b > MAX_BATCH_BYTES or len(cur) >= MAX_BATCH_REQUESTS):
                batches.append(cur)
                cur, size = [], 0
            cur.append(cid)
            size += b
        batches.append(cur)
        results = {}
        for n, cids in enumerate(batches):
            results |= self.one_batch(f"{key}-{n}", {c: jobs[c] for c in cids}, logs)
        return results

    def one_batch(self, key: str, jobs: dict[str, dict], logs: dict[str, dict]) -> dict[str, object]:
        client, path = self.ann.client, self.state / f"{key}.json"
        saved = json.loads(path.read_text()) if path.exists() else None
        worst = sum(self.worst_case(j["params"]) for j in jobs.values())
        if saved and set(saved["custom_ids"]) == set(jobs):
            batch_id = saved["batch_id"]
            print(f"  {key}: resuming batch {batch_id} ({len(jobs)} requests)", flush=True)
            guard(0)  # already paid for or in flight; still refuse to continue if the stop line is passed
        else:
            guard(worst)
            batch = client.messages.batches.create(
                requests=[{"custom_id": c, "params": j["params"]} for c, j in jobs.items()])
            batch_id = batch.id
            path.write_text(json.dumps({"batch_id": batch_id, "custom_ids": list(jobs), "worst_case_usd": worst}))
            print(f"  {key}: submitted {batch_id} ({len(jobs)} requests, worst case ${worst:.2f})", flush=True)
        try:
            while True:
                b = client.messages.batches.retrieve(batch_id)
                if b.processing_status == "ended":
                    break
                time.sleep(POLL)
            out, spent = {}, 0.0
            for r in client.messages.batches.results(batch_id):
                job = jobs.get(r.custom_id)
                if job is None:
                    continue
                if r.result.type != "succeeded":
                    out[r.custom_id] = RuntimeError(f"batch result {r.result.type}"
                                                    + (f": {r.result.error}" if r.result.type == "errored" else ""))
                    continue
                msg = r.result.message
                u = msg.usage
                usd = 0.5 * cost(self.ann.model, u)
                spent += usd
                logs[job["iid"]]["calls"].append({"stop": msg.stop_reason, "in": u.input_tokens, "out": u.output_tokens,
                                                  "cache_write": u.cache_creation_input_tokens or 0,
                                                  "cache_read": u.cache_read_input_tokens or 0, "usd": round(usd, 6),
                                                  "batch": batch_id})
                if msg.stop_reason == "max_tokens":
                    out[r.custom_id] = Truncated(f"output limit reached ({u.output_tokens} tokens)")
                elif msg.stop_reason != "end_turn":
                    out[r.custom_id] = RuntimeError(f"stop_reason {msg.stop_reason}")
                else:
                    try:
                        out[r.custom_id] = json.loads(next(b.text for b in msg.content if b.type == "text"))
                    except (StopIteration, json.JSONDecodeError) as e:
                        out[r.custom_id] = RuntimeError(f"unparseable output: {e!r}")
            if not path.with_suffix(".recorded").exists():  # record once, even if the round is collected again
                record(spent, f"batch {self.out.name} {key} ({batch_id})")
                path.with_suffix(".recorded").write_text(f"{spent:.6f}")
            print(f"  {key}: done, ${spent:.2f}", flush=True)
            return out
        finally:
            if not saved:
                release(worst)

    # ---- a wave of laws ---------------------------------------------------------------------------------------
    def wave(self, iids: list[str]) -> list[str]:
        ann, groups = self.ann, MODES[self.ann.mode]
        w = hashlib.sha1(" ".join(iids).encode()).hexdigest()[:10]  # state key: the same laws resume the same batches
        logs = {i: {"instrument_id": i, "model": ann.model, "effort": ann.effort, "group_effort": ann.group_effort,
                    "mode": ann.mode, "batch": True, "calls": []} for i in iids}
        texts = {i: (TEXTS / f"{i}.txt").read_text(encoding="utf-8") for i in iids}
        leaves: dict[tuple, object] = {}  # (iid, group, path) -> output
        jobs: dict[str, dict] = {}
        for i in iids:
            chunks = split_text(texts[i])
            logs[i]["parts"] = len(chunks)
            for g, fields in groups.items():
                eff = ann.group_effort.get(g)
                if len(chunks) == 1:
                    todo = [((0,), texts[i], "", "")]
                elif g == "overview":
                    extra = f"\n<outline of the whole document>\n{outline(texts[i])}\n</outline>"
                    todo = [((0,), chunks[0], f"1 of {len(chunks)}", extra)]
                else:
                    todo = [((k,), c, f"{k + 1} of {len(chunks)}", "") for k, c in enumerate(chunks)]
                for path, chunk, part, extra in todo:
                    prompt = ann.prompt(i, fields, part if g != "overview" else "")
                    jobs[f"r{len(jobs):06d}"] = {"iid": i, "group": g, "path": path, "chunk": chunk, "depth": 0,
                                                 "eff": eff, "params": self.params(ann.system(i, chunk, part, extra),
                                                                                   prompt, model_schema(fields),
                                                                                   64000, eff)}
        failed: dict[str, str] = {}
        for rnd in range(3):  # first pass, then up to two re-splits of truncated answers
            res = self.run(f"w{w}-groups{rnd}", jobs, logs)
            nxt = {}
            for cid, r in res.items():
                j = jobs[cid]
                if isinstance(r, Truncated) and j["depth"] < 2:
                    logs[j["iid"]].setdefault("resplit", []).append(f"{j['group']} {'.'.join(map(str, j['path']))}")
                    subs = split_text(j["chunk"], max(4000, est_tokens(j["chunk"]) // 2))
                    fields = groups[j["group"]]
                    for k, c in enumerate(subs):
                        part = f"{'.'.join(str(p + 1) for p in j['path'])}.{k + 1}"
                        nxt[f"r{rnd + 1}{len(nxt):06d}"] = {
                            **j, "path": j["path"] + (k,), "chunk": c, "depth": j["depth"] + 1,
                            "params": self.params(ann.system(j["iid"], c, part), ann.prompt(j["iid"], fields, part),
                                                  model_schema(fields), 64000, j["eff"])}
                elif isinstance(r, Exception):
                    failed[j["iid"]] = f"{j['group']}: {r!r}"
                else:
                    leaves[(j["iid"], j["group"], j["path"])] = r
            jobs = nxt
            if not jobs:
                break
        # assemble
        anns = {}
        for i in iids:
            if i in failed:
                continue
            parts = {}
            for g, fields in groups.items():
                outs = [v for (ii, gg, p), v in sorted(leaves.items(), key=lambda x: x[0][2]) if ii == i and gg == g]
                parts[g] = combine(outs, fields) if len(outs) != 1 else outs[0]
            anns[i] = ann.assemble(i, parts, logs[i])
        # quote repairs
        rjobs, bads, docs = {}, {}, {}
        for i, a in anns.items():
            docs[i] = Doc(texts[i])
            bads[i] = bad_quotes(a, docs[i])
            if not bads[i]:
                continue
            prompt, schema = repair_request(i, bads[i])
            chunks = split_text(texts[i])
            for k, c in enumerate(chunks):
                part = f"{k + 1} of {len(chunks)}" if est_tokens(texts[i]) > CHUNK_TOKENS else ""
                rjobs[f"q{len(rjobs):06d}"] = {"iid": i, "k": k,
                                               "params": self.params(ann.system(i, c, part), prompt, schema, 16000, None)}
        rres = self.run(f"w{w}-repairs", rjobs, logs)
        fixes = {i: {} for i in anns}
        for cid in sorted(rres, key=lambda c: rjobs[c]["k"]):
            r, i = rres[cid], rjobs[cid]["iid"]
            if isinstance(r, Exception):
                continue  # unrepaired quotes are dropped below
            for f in r["fixes"]:
                if f["quote"] and f["path"] not in fixes[i]:
                    fixes[i][f["path"]] = f["quote"]
        lines = []
        for i, a in anns.items():
            logs[i]["quote_repairs"] = apply_fixes(a, bads[i], fixes[i], docs[i]) if bads[i] else {"failed": 0}
            log = ann.write(i, a, logs[i])
            lines.append(f"{i}: ${log['usd']:.4f}, quotes {log['quote_repairs']}, errors {len(log['errors'])}")
        for i, why in failed.items():
            logs[i]["error"] = why
            (self.out / f"{i}.log.json").write_text(json.dumps(logs[i], indent=1))
            lines.append(f"{i}: FAILED {why}")
        return lines


def run_batch(ann: Annotator, iids: list[str], wave: int) -> None:
    br = BatchRun(ann)
    todo = [i for i in iids if not (ann.out / f"{i}.json").exists()]
    print(f"{len(todo)} laws to annotate in batch mode ({len(iids) - len(todo)} already done)", flush=True)
    for w in range(0, len(todo), wave):
        ids = todo[w:w + wave]
        print(f"wave {w // wave}: {len(ids)} laws", flush=True)
        for line in br.wave(ids):
            print(line, flush=True)
