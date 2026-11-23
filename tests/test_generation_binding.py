"""温谱导出代际绑定：错代导出 / 影子混册 / 并发争用 三红夹具。

复现口径（封批代落后于导出请求）：
- 夹具经正式 API 走 入库→封批 造出当前封批代 G2 的已封批批次；
- 错代请求分别模拟“请求代超前（封批代落后于请求）”与“上一代冒充当代”。

路径口径：甲路径（导出请求必须带当前封批代，校验失败正式行零增拒收）。
本文件不引入任何影子表/影子状态（乙路径不采用）。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _formal_count(batch_id: int) -> int:
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM spectrum_exports WHERE batch_id=?",
            (batch_id,),
        ).fetchone()["c"]


def _sealed_batch_g2(code: str = "TB-GENLAG-001") -> dict:
    """复现夹具：封批代落后于导出请求的前置状态——当前封批代 G2。"""
    c = TestClient(app)
    h = _auth(c)
    ch = next(
        x
        for x in c.get("/api/chambers", headers=h).json()["items"]
        if x["chamber_code"] == "CK-HN-03"
    )
    probe = c.get(
        f"/api/probes?chamber_id={ch['id']}", headers=h
    ).json()["items"][0]
    points = [
        {"t": "10:00", "c": 3.2},
        {"t": "11:00", "c": 4.8},
        {"t": "12:00", "c": 5.1},
    ]
    batch = c.post(
        "/api/batches",
        headers=h,
        json={
            "chamber_id": ch["id"],
            "probe_id": probe["id"],
            "cargo_ref": "代际绑定复现货载",
            "temp_points": points,
            "batch_code": code,
        },
    ).json()
    seal = c.post("/api/seals", headers=h, json={"batch_id": batch["id"]}).json()
    assert seal["generation"] == 2
    return {"id": batch["id"], "code": code, "generation": 2}


# 失败测甲：盯错代仍能下正式温谱
def test_a_wrong_generation_export_rejected_zero_formal_rows():
    c = TestClient(app)
    h = _auth(c)
    b = _sealed_batch_g2()

    # 请求代 G3 超前于当前封批代 G2（封批代落后于导出请求）
    ahead = c.post(
        "/api/spectrum/export",
        headers=h,
        json={"batch_id": b["id"], "generation": 3},
    )
    assert ahead.status_code == 409
    assert _formal_count(b["id"]) == 0

    # 上一代 G1 冒充当代 G2
    stale = c.post(
        "/api/spectrum/export",
        headers=h,
        json={"batch_id": b["id"], "generation": 1},
    )
    assert stale.status_code == 409
    assert _formal_count(b["id"]) == 0

    # 只有当前封批代 G2 可出正式温谱，且正式行绑定代=2
    ok = c.post(
        "/api/spectrum/export",
        headers=h,
        json={"batch_id": b["id"], "generation": 2},
    )
    assert ok.status_code == 200
    assert ok.json()["export_ref"].endswith("-G2")
    rows = c.get("/api/spectrum", headers=h).json()["items"]
    mine = [x for x in rows if x["batch_id"] == b["id"]]
    assert len(mine) == 1
    assert mine[0]["generation"] == 2
    assert _formal_count(b["id"]) == 1


# 失败测乙：盯影子温谱未晋升却出现在正式册（甲路径下：系统不得产生影子，
# 正式册只能含当前封批代正式行；错代尝试零增，互证须识别错代行）
def test_b_no_unpromoted_shadow_in_formal_book():
    c = TestClient(app)
    h = _auth(c)
    b = _sealed_batch_g2("TB-GENLAG-002")

    # 错代导出尝试：拒绝且正式册零增（不得落任何“待晋升影子”行）
    r = c.post(
        "/api/spectrum/export",
        headers=h,
        json={"batch_id": b["id"], "generation": 1},
    )
    assert r.status_code == 409
    listed = c.get("/api/spectrum", headers=h).json()["items"]
    assert [x for x in listed if x["batch_id"] == b["id"]] == []
    assert _formal_count(b["id"]) == 0

    # 模拟旧代曲线混入正式册（运维直插旧代行）：互证须识别为错代，
    # 且正式列表须透明暴露该行绑定代，不得只按条数声称一致。
    with connect() as conn:
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(spectrum_exports)").fetchall()
        }
        if "generation" in cols:
            conn.execute(
                """INSERT INTO spectrum_exports(batch_id,export_ref,point_count,
                   checksum,status,created_at,generation)
                   VALUES (?,?,?,?,?,?,?)""",
                (b["id"], f"SP-{b['code']}-G1", 2, "deadbeefdeadbeef",
                 "已导出", "2026-11-01T00:00:00", 1),
            )
        else:
            conn.execute(
                """INSERT INTO spectrum_exports(batch_id,export_ref,point_count,
                   checksum,status,created_at) VALUES (?,?,?,?,?,?)""",
                (b["id"], f"SP-{b['code']}-G1", 2, "deadbeefdeadbeef",
                 "已导出", "2026-11-01T00:00:00"),
            )
        conn.commit()

    listed = c.get("/api/spectrum", headers=h).json()["items"]
    leaked = [x for x in listed if x["export_ref"] == f"SP-{b['code']}-G1"][0]
    assert leaked["generation"] == 1

    proof = c.get(f"/api/proof/batch/{b['code']}", headers=h).json()
    assert proof["generation"] == 2
    assert proof["seal_generation"] == 2
    assert proof["spectrum_generations"] == [1]
    assert proof["spectrum_matches_seal"] is False


# 争用失败测：双人同批并发导出无锁
def test_c_concurrent_same_batch_export_single_winner():
    b = _sealed_batch_g2("TB-GENLAG-003")
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def worker(user: str) -> None:
        c = TestClient(app)
        h = _auth(c, user)
        barrier.wait()
        r = c.post(
            "/api/spectrum/export",
            headers=h,
            json={"batch_id": b["id"], "generation": 2},
        )
        results.append({"user": user, "status": r.status_code})

    t1 = threading.Thread(target=worker, args=("admin",))
    t2 = threading.Thread(target=worker, args=("ops",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = sorted(x["status"] for x in results)
    assert statuses == [200, 409]
    assert _formal_count(b["id"]) == 1

    c = TestClient(app)
    h = _auth(c)
    contention = c.get("/api/contention", headers=h).json()["items"]
    assert any(x["lock_key"] == f"batch:{b['id']}" for x in contention)
    locks = c.get("/api/locks", headers=h).json()["items"]
    assert [x for x in locks if x["lock_key"] == f"batch:{b['id']}"] == []
