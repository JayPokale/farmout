files_api.py:24 | high | SQL injection: `owner` is interpolated into the query string | owner = `x' OR '1'='1` lists every user's files; `UNION SELECT` reads other tables | Use a parameterized query: `WHERE owner = ?`, `(owner,)`.

files_api.py:42 | high | Path traversal: `name` and `owner` go into `os.path.join` unchecked, and an absolute `name` replaces STORAGE. There's also no check that the caller owns the file. | name = `../../etc/passwd` or `/etc/passwd`; any caller can read another owner's files via `/files/<other>/<name>` | Authenticate the caller and require owner == user. Resolve with `os.path.realpath` and check `commonpath([STORAGE, path]) == STORAGE`. Better, look the file up by id in the DB.

files_api.py:33 | high | Off-by-one in pagination: the docstring says pages start at 1, but `start = page * PAGE_SIZE` skips the first page | page=1 returns items 20–39, and page 1 of a one-page listing returns an empty list; page=0 returns the first page | `start = (page - 1) * PAGE_SIZE`. Also reject page < 1, since a negative page gives a wrong slice.

files_api.py:49 | high | `fetchone()` returns None for a missing id, so `row["owner"]` raises TypeError | delete with a nonexistent or already-deleted file_id | Check `if row is None`, then raise a not-found error.

files_api.py:53 | high | Path traversal on delete: `row["name"]` is joined into the path and passed to `os.remove` without validation | A stored name like `../x` or an absolute path deletes arbitrary files | Validate the name when it is stored, and confirm the resolved path stays under STORAGE before removing it.

files_api.py:54 | medium | Quota read-modify-write is not atomic, and the row may be missing | Two concurrent deletes both read the same `used` and the quota is under-decremented; an owner with no quota row raises TypeError on `["used"]` | Use `UPDATE quota SET used = used - ? WHERE owner = ?` in one transaction, and handle a missing row.

files_api.py:55 | high | Data loss and inconsistency: the quota is updated and the file is removed before the DB row is deleted, and there is no commit or rollback | `os.remove` or the DELETE fails, or the process crashes mid-way: the file is gone but the row stays, or the quota is decremented while the file remains | Delete the DB row and update the quota in one transaction, commit, and remove the file afterwards. Ignore FileNotFoundError.

files_api.py:58 | medium | No `db.commit()` anywhere: sqlite3 opens an implicit transaction on UPDATE/DELETE | Closing the connection after `delete` without a commit discards the quota update and the row deletion, though the file is already removed | Call `db.commit()` after the DB changes, or use `with db:`.

files_api.py:29 | medium | The `url` is built from the unescaped `name` and `owner`, and it is not URL-encoded | A name containing `/`, `?`, `#`, or `../` produces a broken or traversal-prone link | Use `urllib.parse.quote` on each path segment.