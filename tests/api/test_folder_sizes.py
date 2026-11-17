from tests.api.conftest import path, upload
from tests.factories import PDF

A = b"a" * 100
B = b"b" * 250
C = b"c" * 7


async def put(client, storage, rest: str, data: bytes, **options) -> dict:
    task = await upload(client, path(storage, rest), data, **options)
    assert task["status"] == "done", task
    return task["file"]


async def listing(client, storage, rest: str = "") -> dict:
    response = await client.get("/folders", params={"path": path(storage, rest)})
    assert response.status_code == 200, response.text
    return response.json()


def sizes(result: dict) -> dict[str, tuple[int, int]]:
    return {item["name"]: (item["files"], item["size"]) for item in result["folders"]}


async def test_folder_sizes_cover_the_whole_subtree(client, storage):
    await put(client, storage, "root.txt", C)
    await put(client, storage, "Отпуск/a.txt", A)
    await put(client, storage, "Отпуск/2026/b.txt", B)
    await put(client, storage, "Отпуск/2026/deep/c.txt", C)
    await put(client, storage, "docs/report.pdf", PDF)
    await client.post("/folders", json={"path": path(storage, "empty")})
    root = await listing(client, storage)
    assert sizes(root) == {
        "Отпуск": (3, len(A) + len(B) + len(C)),
        "docs": (1, len(PDF)),
        "empty": (0, 0),
    }
    assert root["total_files"] == 5
    assert root["size"] == len(A) + len(B) + 2 * len(C) + len(PDF)
    trip = await listing(client, storage, "Отпуск")
    assert sizes(trip) == {"2026": (2, len(B) + len(C))}
    assert (trip["total_files"], trip["size"]) == (3, len(A) + len(B) + len(C))
    deep = await listing(client, storage, "Отпуск/2026/deep")
    assert (deep["folders"], deep["total_files"], deep["size"]) == ([], 1, len(C))


async def test_folder_sizes_treat_like_wildcards_literally(client, storage):
    await put(client, storage, "a_b/x.txt", A)
    await put(client, storage, "axb/y.txt", B)
    await put(client, storage, "a%/z.txt", C)
    assert sizes(await listing(client, storage)) == {
        "a%": (1, len(C)),
        "a_b": (1, len(A)),
        "axb": (1, len(B)),
    }
    assert (await listing(client, storage, "a_b"))["size"] == len(A)


async def test_folder_size_is_plaintext_size_of_encrypted_files(client, storage):
    await put(client, storage, "box/s.txt", B, encrypt="true")
    await put(client, storage, "box/p.txt", A, encrypt="false")
    assert sizes(await listing(client, storage)) == {"box": (2, len(A) + len(B))}


async def test_folder_sizes_count_only_readable_files(client, storage, make_user):
    await put(client, storage, "top.txt", C)
    await put(client, storage, "team/inbox/a.txt", A)
    await put(client, storage, "team/inbox/sub/b.txt", B)
    await put(client, storage, "team/secret/c.txt", B)
    await put(client, storage, "team/flat/d.txt", A)
    await put(client, storage, "team/flat/inner/e.txt", B)
    user = await make_user(
        "sizes",
        "user",
        {"path": path(storage, "team/inbox")},
        {"path": path(storage, "team/flat"), "recursive": False},
    )
    root = await listing(user, storage)
    assert sizes(root) == {"team": (3, 2 * len(A) + len(B))}
    assert (root["total_files"], root["size"]) == (3, 2 * len(A) + len(B))
    team = await listing(user, storage, "team")
    assert sizes(team) == {"flat": (1, len(A)), "inbox": (2, len(A) + len(B))}
    assert team["files"] == []
    flat = await listing(user, storage, "team/flat")
    assert (flat["folders"], flat["total_files"], flat["size"]) == ([], 1, len(A))
    full = await listing(client, storage, "team")
    assert sizes(full) == {
        "flat": (2, len(A) + len(B)),
        "inbox": (2, len(A) + len(B)),
        "secret": (1, len(B)),
    }
