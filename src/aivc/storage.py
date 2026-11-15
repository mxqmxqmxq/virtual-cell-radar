"""Monthly-partitioned JSONL storage for papers."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Paper


class PaperStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "papers"

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("*.jsonl")) if self.dir.exists() else []

    def _read_file(self, p: Path) -> list[Paper]:
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Paper.model_validate_json(line))
        return out

    def load_all(self) -> list[Paper]:
        res: list[Paper] = []
        for p in self._files():
            res.extend(self._read_file(p))
        return res

    def _write_file(self, p: Path, papers: list[Paper]) -> None:
        papers = sorted(papers, key=lambda x: x.id)
        text = "".join(
            json.dumps(x.model_dump(mode="json"), ensure_ascii=False) + "\n" for x in papers
        )
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def upsert(self, papers: list[Paper]) -> tuple[int, int]:
        self.dir.mkdir(parents=True, exist_ok=True)
        files: dict[Path, dict[str, Paper]] = {p: {x.id: x for x in self._read_file(p)} for p in self._files()}
        where: dict[str, Path] = {i: p for p, d in files.items() for i in d}
        n_new = n_upd = 0
        dirty: set[Path] = set()
        for paper in papers:
            if paper.id in where:
                f = where[paper.id]
                if files[f][paper.id] != paper:
                    n_upd += 1
                    dirty.add(f)
                files[f][paper.id] = paper
            else:
                d = paper.published or paper.first_seen
                f = self.dir / (d.strftime("%Y-%m") + ".jsonl")
                files.setdefault(f, {})[paper.id] = paper
                where[paper.id] = f
                n_new += 1
                dirty.add(f)
        for f in dirty:
            self._write_file(f, list(files[f].values()))
        return n_new, n_upd

    def delete(self, ids: list[str]) -> int:
        drop = set(ids)
        n = 0
        for f in self._files():
            papers = self._read_file(f)
            keep = [x for x in papers if x.id not in drop]
            if len(keep) != len(papers):
                n += len(papers) - len(keep)
                self._write_file(f, keep)
        return n


def load_overlay(data_dir: Path, local_data_dir: Path) -> list[Paper]:
    """只读叠加：公开库为底，local 库同 id 记录只贡献 local_file 与 tags（并集，公开在前），
    local 独有的记录整条追加。不写任何文件。"""
    public = PaperStore(data_dir).load_all()
    local = {p.id: p for p in PaperStore(local_data_dir).load_all()}
    out: list[Paper] = []
    seen: set[str] = set()
    for p in public:
        seen.add(p.id)
        lp = local.get(p.id)
        if lp is None:
            out.append(p)
            continue
        tags = list(p.tags) + [t for t in lp.tags if t not in p.tags]
        out.append(p.model_copy(update={"local_file": lp.local_file or p.local_file, "tags": tags}))
    out.extend(p for pid, p in local.items() if pid not in seen)
    return out
