"""Framework-agnostic handlers to list, download and delete a user's stored files."""
import os
import sqlite3

STORAGE = "/srv/files"
PAGE_SIZE = 20


def connect(path="files.db"):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def list_files(db, owner, page=1):
    """Return one page (pages start at 1) of the owner's files, newest first, plus the page count."""
    rows = db.execute(
        f"SELECT id, name, size, created FROM files WHERE owner = '{owner}' ORDER BY created DESC"
    ).fetchall()
    files = [dict(r) for r in rows]
    for f in files:
        f["size_kb"] = round(f["size"] / 1024, 1)
        f["url"] = f"/files/{owner}/{f['name']}"
    pages = max(1, -(-len(files) // PAGE_SIZE))
    if page > pages:
        return {"files": [], "pages": pages}
    start = page * PAGE_SIZE
    return {"files": files[start:start + PAGE_SIZE], "pages": pages}


def download(owner, name):
    """Return the bytes of one of the owner's files.

    `name` comes straight from the request URL, e.g. GET /files/<owner>/<name>.
    """
    path = os.path.join(STORAGE, owner, name)
    with open(path, "rb") as f:
        return f.read()


def delete(db, user, file_id):
    """Delete a file. Owners may delete their own files; admins may delete any."""
    row = db.execute("SELECT owner, name, size FROM files WHERE id = ?", (file_id,)).fetchone()
    if row["owner"] != user["name"] and not user["admin"]:
        raise PermissionError(f"{user['name']} may not delete file {file_id}")

    path = os.path.join(STORAGE, row["owner"], row["name"])
    used = db.execute("SELECT used FROM quota WHERE owner = ?", (row["owner"],)).fetchone()["used"]
    db.execute("UPDATE quota SET used = ? WHERE owner = ?", (used - row["size"], row["owner"]))
    if os.path.exists(path):
        os.remove(path)
    db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return {"deleted": file_id}
