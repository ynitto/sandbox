"""埋め込み段構え（設計 docs/designs/ltm-use-embedding-recall-design.md）の契約テスト。

ollama は呼ばない（embed_texts を差し替える）。見たいのは 4 つ:
  1. 索引が無ければ recall は従来と同じ道を通る（埋め込みを一切呼ばない）
  2. TF-IDF が自信を持てる（最上位コサイン ≥ しきい値）なら埋め込みを呼ばない
  3. 自信が無いときだけ埋め込みで採点し直し、順位は埋め込みのコサインに従う
  4. ollama が落ちていても recall は失敗せず TF-IDF の結果を返す

実行: python3 -m unittest discover -s .github/skills/ltm-use/tests
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import embeddings  # noqa: E402
import memory_utils  # noqa: E402
import recall_memory  # noqa: E402
import similarity  # noqa: E402


def _mem(mem_id: str, title: str, summary: str, body: str) -> str:
    return ("---\n" f"id: {mem_id}\n" f'title: "{title}"\n' f'summary: "{summary}"\n'
            "created: 2026-01-01\nupdated: 2026-01-01\nstatus: active\nscope: home\n"
            f"access_count: 0\n---\n\n{body}\n")


MEMORIES = {
    "mem-jwt": ("JWT の有効期限を 15 分にした理由", "トークン期限とリフレッシュの設計判断",
                "アクセストークンは 15 分、リフレッシュは 7 日。"),
    "mem-gitkeep": ("wiki-use 一括取込で空ファイルが .gitkeep と衝突する", "空ファイルの置き場の印との衝突",
                    "中身の無い md が .gitkeep と同じ扱いになり取り込みが落ちた。"),
    "mem-lock": ("kiro-loop を並列に回すときの排他", "同時実行時のロック取得の手順",
                 "flock でループ単位に排他し、取り合いを防いだ。"),
}


class _Corpus:
    """一時 memory_dir にインデックス + コーパスを作る。埋め込み索引は任意。"""

    def __init__(self, with_embeddings: bool):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = str(Path(self.tmp.name) / "home")
        for mem_id, (title, summary, body) in MEMORIES.items():
            p = Path(self.dir) / "general" / f"{mem_id}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_mem(mem_id, title, summary, body), encoding="utf-8")
        memory_utils.refresh_index(self.dir)
        similarity.build_corpus(self.dir)
        if with_embeddings:
            store = {"version": 1, "model": "fake-embed", "built_at": "", "vectors": {}}
            # 直交する 3 本。クエリ側の偽ベクトルで「どれに近いか」を自在に決められる。
            for i, mem_id in enumerate(MEMORIES):
                vec = [0.0, 0.0, 0.0]
                vec[i] = 1.0
                text = embeddings.memory_text(str(Path(self.dir) / "general" / f"{mem_id}.md"))
                store["vectors"][mem_id] = {"hash": embeddings.text_hash(text), "vec": vec}
            embeddings.save_embeddings(self.dir, store)

    def close(self):
        self.tmp.cleanup()


def _search(memory_dir: str, query: str) -> list[dict]:
    keywords = query.split()
    return recall_memory.search_with_index(memory_dir, keywords, None, 5)


class EmbeddingCascadeTest(unittest.TestCase):
    def setUp(self):
        self.calls: list[list[str]] = []
        embeddings._warned = False

    def _fake_embed(self, vec):
        def fake(texts, model, timeout=0):
            self.calls.append(list(texts))
            return [vec for _ in texts]
        return fake

    def test_without_index_recall_is_unchanged_and_never_embeds(self):
        c = _Corpus(with_embeddings=False)
        self.addCleanup(c.close)
        with mock.patch.object(embeddings, "embed_texts", self._fake_embed([0, 0, 1])):
            results = _search(c.dir, "同時に走らせたときの取り合い")
        self.assertEqual(self.calls, [])
        self.assertTrue(all(r["ranker"] == "tfidf" for r in results))

    def test_confident_tfidf_does_not_call_embeddings(self):
        c = _Corpus(with_embeddings=True)
        self.addCleanup(c.close)
        with mock.patch.object(embeddings, "embed_texts", self._fake_embed([0, 0, 1])):
            results = _search(c.dir, "JWT 有効期限 15 分")      # 用語そのまま（lexical）
        self.assertEqual(self.calls, [])
        self.assertEqual(results[0]["entry"]["id"], "mem-jwt")
        self.assertEqual(results[0]["ranker"], "tfidf")

    def test_unconfident_tfidf_reranks_by_embedding(self):
        c = _Corpus(with_embeddings=True)
        self.addCleanup(c.close)
        # 用語を忘れた訊き方。TF-IDF は当てられない → 埋め込み（偽ベクトルは mem-lock に最近接）。
        with mock.patch.object(embeddings, "embed_texts", self._fake_embed([0, 0, 1])):
            results = _search(c.dir, "走らせたとき 取り合い 防ぐ")
        self.assertEqual(len(self.calls), 1, "クエリの埋め込みは 1 回だけ")
        self.assertEqual(results[0]["entry"]["id"], "mem-lock")
        self.assertEqual(results[0]["ranker"], "embedding")
        # 埋め込み段では body の語一致を足さない（コサインの差を上書きしないため）
        self.assertAlmostEqual(results[0]["score"], 1.0, places=6)

    def test_ollama_failure_falls_back_to_tfidf_without_raising(self):
        c = _Corpus(with_embeddings=True)
        self.addCleanup(c.close)

        def broken(texts, model, timeout=0):
            raise OSError("connection refused")
        with mock.patch.object(embeddings, "embed_texts", broken):
            results = _search(c.dir, "走らせたとき 取り合い 防ぐ")
        self.assertTrue(all(r["ranker"] == "tfidf" for r in results))

    def test_build_embeds_only_missing_and_stale_rows(self):
        c = _Corpus(with_embeddings=True)
        self.addCleanup(c.close)
        # 1 件だけ本文を書き換える → その 1 件だけ stale で埋め直される
        p = Path(c.dir) / "general" / "mem-jwt.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n追記\n", encoding="utf-8")
        memory_utils.refresh_index(c.dir)
        with mock.patch.object(embeddings, "embed_texts", self._fake_embed([1, 1, 1])):
            st = embeddings.build(c.dir, "fake-embed")
        self.assertEqual(st["embedded"], 1)
        self.assertEqual(st["indexed"], 3)
        self.assertEqual(sum(len(x) for x in self.calls), 1)
        self.assertEqual(embeddings.status(c.dir)["stale"], 0)


if __name__ == "__main__":
    unittest.main()
