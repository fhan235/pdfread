"""文库与高亮批注的本地持久化(SQLite)。

数据放在用户配置目录(而非缓存目录): 文库条目、文件夹、标签和高亮
属于用户数据, 清理缓存时不应丢失。

- 文档以绝对路径去重, 重复打开只更新最近阅读时间。
- 高亮坐标以 PDF 点(pt)存储, 与渲染 DPI 无关, 前端按当前缩放换算。
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path

VALID_COLORS = {"yellow", "green", "blue", "pink"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    added REAL NOT NULL,
    last_opened REAL
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS document_tags (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);
CREATE TABLE IF NOT EXISTS qa_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL,
    hist TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_qa_doc ON qa_sessions(document_id, id);
CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    rects TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT 'yellow',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hl_doc ON highlights(document_id, page);
"""


class Library:
    """线程安全的文库数据库。"""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # 旧库迁移: 补阅读进度/总页数/内容哈希列
            for ddl in ("ALTER TABLE documents ADD COLUMN"
                        " progress INTEGER NOT NULL DEFAULT 0",
                        "ALTER TABLE documents ADD COLUMN"
                        " pages INTEGER NOT NULL DEFAULT 0",
                        # 内容哈希用于查重; 允许为空(旧记录与哈希失败的情况),
                        # 不加 UNIQUE —— 用户可以主动选择"仍然导入一份"
                        "ALTER TABLE documents ADD COLUMN"
                        " sha256 TEXT NOT NULL DEFAULT ''"):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            # 查重按哈希命中, 建索引避免全表扫描
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_sha"
                               " ON documents(sha256)")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 文件夹 ----------

    def add_folder(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("文件夹名不能为空")
        if len(name) > 60:
            raise ValueError("文件夹名过长")
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO folders (name, created) VALUES (?, ?)",
                    (name, time.time()),
                )
            except sqlite3.IntegrityError:
                raise ValueError("同名文件夹已存在") from None
            self._conn.commit()
            return {"id": cur.lastrowid, "name": name}

    def rename_folder(self, folder_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("文件夹名不能为空")
        with self._lock:
            try:
                cur = self._conn.execute(
                    "UPDATE folders SET name = ? WHERE id = ?", (name, folder_id)
                )
            except sqlite3.IntegrityError:
                raise ValueError("同名文件夹已存在") from None
            self._conn.commit()
            if cur.rowcount == 0:
                raise ValueError("文件夹不存在")

    def delete_folder(self, folder_id: int) -> None:
        """删除文件夹; 其中文档移回未分类(ON DELETE SET NULL)。"""
        with self._lock:
            self._conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
            self._conn.commit()

    # ---------- 文档 ----------

    def upsert_document(self, path: str, name: str, title: str = "",
                        pages: int = 0, sha256: str = "") -> int:
        """按绝对路径登记文档, 重复打开只刷新时间; 返回文库文档 id。"""
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM documents WHERE path = ?", (path,)
            ).fetchone()
            if row:
                doc_id = row[0]
                self._conn.execute(
                    "UPDATE documents SET last_opened = ?,"
                    " name = ?, title = CASE WHEN ? != '' THEN ? ELSE title END,"
                    " pages = CASE WHEN ? > 0 THEN ? ELSE pages END,"
                    " sha256 = CASE WHEN ? != '' THEN ? ELSE sha256 END"
                    " WHERE id = ?",
                    (now, name, title, title, pages, pages,
                     sha256, sha256, doc_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO documents (path, name, title, pages, added,"
                    " last_opened, sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (path, name, title or "", pages, now, now, sha256 or ""),
                )
                doc_id = cur.lastrowid
            self._conn.commit()
            return doc_id

    def find_by_hash(self, sha256: str, exclude_path: str = "") -> dict | None:
        """按内容哈希找已入库的同一文件, 用于导入查重。

        exclude_path 用于排除"就是这个文件本身"(同路径重复打开不是重复导入,
        不该打扰用户); 只有换了路径的同内容文件才算重复。
        返回最近打开的一条。
        """
        if not sha256:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id, path, name, title, added, last_opened, pages,"
                " progress FROM documents"
                " WHERE sha256 = ? AND path != ?"
                " ORDER BY last_opened DESC LIMIT 1",
                (sha256, exclude_path),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "path": row[1], "name": row[2], "title": row[3],
                "added": row[4], "last_opened": row[5], "pages": row[6],
                "progress": row[7], "exists": Path(row[1]).is_file()}

    def set_progress(self, doc_id: int, page: int) -> None:
        """记录阅读进度(页码)。"""
        if not isinstance(page, int) or page < 0 or page > 100000:
            raise ValueError("进度页码无效")
        with self._lock:
            self._conn.execute(
                "UPDATE documents SET progress = ? WHERE id = ?",
                (page, doc_id))
            self._conn.commit()

    def get_progress(self, doc_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT progress FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def document_path(self, doc_id: int) -> tuple[str, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT path, name FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return (row[0], row[1]) if row else None

    def move_document(self, doc_id: int, folder_id: int | None) -> None:
        with self._lock:
            if folder_id is not None:
                exists = self._conn.execute(
                    "SELECT 1 FROM folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if not exists:
                    raise ValueError("文件夹不存在")
            cur = self._conn.execute(
                "UPDATE documents SET folder_id = ? WHERE id = ?",
                (folder_id, doc_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                raise ValueError("文档不存在")

    def remove_document(self, doc_id: int) -> None:
        """从文库移除(不删除磁盘文件); 高亮随外键级联删除。"""
        with self._lock:
            self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.commit()

    def set_tags(self, doc_id: int, names: list[str]) -> None:
        clean = []
        for raw in names:
            name = str(raw).strip()
            if name and len(name) <= 30 and name not in clean:
                clean.append(name)
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if not exists:
                raise ValueError("文档不存在")
            self._conn.execute(
                "DELETE FROM document_tags WHERE document_id = ?", (doc_id,)
            )
            for name in clean:
                self._conn.execute(
                    "INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,)
                )
                tag_id = self._conn.execute(
                    "SELECT id FROM tags WHERE name = ?", (name,)
                ).fetchone()[0]
                self._conn.execute(
                    "INSERT OR IGNORE INTO document_tags (document_id, tag_id)"
                    " VALUES (?, ?)",
                    (doc_id, tag_id),
                )
            # 清理不再被任何文档使用的标签
            self._conn.execute(
                "DELETE FROM tags WHERE id NOT IN"
                " (SELECT DISTINCT tag_id FROM document_tags)"
            )
            self._conn.commit()

    # ---------- 高亮 ----------

    def add_highlight(self, doc_id: int, page: int, rects: list,
                      color: str = "yellow") -> dict:
        if color not in VALID_COLORS:
            color = "yellow"
        if not isinstance(page, int) or page < 1:
            raise ValueError("页码无效")
        if not isinstance(rects, list) or not rects or len(rects) > 200:
            raise ValueError("高亮区域无效")
        norm = []
        for r in rects:
            if not isinstance(r, (list, tuple)) or len(r) != 4:
                raise ValueError("高亮矩形格式无效")
            try:
                x0, y0, x1, y1 = (float(v) for v in r)
            except (TypeError, ValueError):
                raise ValueError("高亮坐标必须是数字") from None
            if not all(map(math.isfinite, (x0, y0, x1, y1))):
                raise ValueError("高亮坐标必须是有限数字")  # NaN/Inf 会破坏 JSON 序列化
            if x1 <= x0 or y1 <= y0 or max(x0, y0, x1, y1) > 10000:
                raise ValueError("高亮矩形范围无效")
            norm.append([round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)])
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if not exists:
                raise ValueError("文档不存在")
            cur = self._conn.execute(
                "INSERT INTO highlights (document_id, page, rects, color, created)"
                " VALUES (?, ?, ?, ?, ?)",
                (doc_id, page, json.dumps(norm), color, time.time()),
            )
            self._conn.commit()
            return {"id": cur.lastrowid, "page": page, "rects": norm,
                    "color": color}

    def delete_highlight(self, highlight_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM highlights WHERE id = ?",
                               (highlight_id,))
            self._conn.commit()

    def list_highlights(self, doc_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, page, rects, color FROM highlights"
                " WHERE document_id = ? ORDER BY page, id",
                (doc_id,),
            ).fetchall()
        return [{"id": r[0], "page": r[1], "rects": json.loads(r[2]),
                 "color": r[3]} for r in rows]

    # ---------- 汇总 ----------

    def overview(self) -> dict:
        """文库首页一次取全: 文件夹 / 标签 / 文档(含各自标签与高亮数)。"""
        with self._lock:
            folders = [
                {"id": r[0], "name": r[1]}
                for r in self._conn.execute(
                    "SELECT id, name FROM folders ORDER BY name")
            ]
            tags = [
                {"id": r[0], "name": r[1], "count": r[2]}
                for r in self._conn.execute(
                    "SELECT t.id, t.name, COUNT(dt.document_id) FROM tags t"
                    " JOIN document_tags dt ON dt.tag_id = t.id"
                    " GROUP BY t.id ORDER BY t.name")
            ]
            docs = []
            for r in self._conn.execute(
                    "SELECT d.id, d.path, d.name, d.title, d.folder_id,"
                    " d.added, d.last_opened,"
                    " (SELECT COUNT(*) FROM highlights h"
                    "  WHERE h.document_id = d.id) AS hl,"
                    " d.progress, d.pages"
                    " FROM documents d ORDER BY d.last_opened DESC"):
                tag_names = [t[0] for t in self._conn.execute(
                    "SELECT t.name FROM tags t"
                    " JOIN document_tags dt ON dt.tag_id = t.id"
                    " WHERE dt.document_id = ? ORDER BY t.name", (r[0],))]
                docs.append({
                    "id": r[0], "path": r[1], "name": r[2], "title": r[3],
                    "folder_id": r[4], "added": r[5], "last_opened": r[6],
                    "highlights": r[7], "tags": tag_names,
                    "progress": r[8], "pages": r[9],
                    "exists": Path(r[1]).is_file(),
                })
        return {"folders": folders, "tags": tags, "documents": docs}

    # ---------- AI 问答会话 ----------
    # 会话按文库文档 id 绑定并持久化, 刷新页面/重开应用都还在;
    # 文档从库中移除时随外键级联删除。

    MAX_QA_SESSIONS = 20        # 每个文档保留的对话数上限
    MAX_QA_MESSAGES = 200       # 单个对话保留的消息条数上限
    MAX_QA_CHARS = 20000        # 单条消息的字符上限

    def _clip_hist(self, hist: list) -> list:
        """裁剪历史到安全体积: 只认 user/assistant, 超长截断, 过多丢旧的。"""
        out = []
        for m in hist if isinstance(hist, list) else []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            out.append({"role": role, "content": content[:self.MAX_QA_CHARS]})
        return out[-self.MAX_QA_MESSAGES:]

    def list_qa_sessions(self, doc_id: int) -> list[dict]:
        """取某文档的全部对话(按创建顺序)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created, updated, hist FROM qa_sessions"
                " WHERE document_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
        out = []
        for r in rows:
            try:
                hist = json.loads(r[4])
            except Exception:
                hist = []
            out.append({"id": r[0], "title": r[1], "created": r[2],
                        "updated": r[3], "hist": self._clip_hist(hist)})
        return out

    def save_qa_session(self, doc_id: int, session_id: int | None,
                        hist: list, title: str = "") -> int:
        """新建或更新一个对话, 返回会话 id。

        session_id 为空表示新建; 新建时若已超过上限, 删掉最旧的那些。
        """
        hist = self._clip_hist(hist)
        payload = json.dumps(hist, ensure_ascii=False)
        now = time.time()
        with self._lock:
            if session_id:
                cur = self._conn.execute(
                    "UPDATE qa_sessions SET hist = ?, title = ?, updated = ?"
                    " WHERE id = ? AND document_id = ?",
                    (payload, title[:120], now, session_id, doc_id),
                )
                if cur.rowcount:
                    self._conn.commit()
                    return session_id
                # 该会话已被删除(或不属于此文档): 落回新建, 不静默丢数据
            cur = self._conn.execute(
                "INSERT INTO qa_sessions (document_id, title, created, updated,"
                " hist) VALUES (?, ?, ?, ?, ?)",
                (doc_id, title[:120], now, now, payload),
            )
            new_id = cur.lastrowid
            self._conn.execute(
                "DELETE FROM qa_sessions WHERE document_id = ? AND id NOT IN ("
                " SELECT id FROM qa_sessions WHERE document_id = ?"
                " ORDER BY id DESC LIMIT ?)",
                (doc_id, doc_id, self.MAX_QA_SESSIONS),
            )
            self._conn.commit()
            return new_id

    def delete_qa_session(self, doc_id: int, session_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM qa_sessions WHERE id = ? AND document_id = ?",
                (session_id, doc_id))
            self._conn.commit()
