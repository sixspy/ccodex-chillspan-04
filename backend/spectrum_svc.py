"""温谱导出（甲路径：导出请求必须绑定当前封批代，错代零增拒收）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, try_acquire
from models import SpectrumIn

router = APIRouter(tags=["spectrum"])


@router.get("/api/spectrum")
def list_exports(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, b.batch_code FROM spectrum_exports s
               JOIN temp_batches b ON b.id=s.batch_id ORDER BY s.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/spectrum/export")
def export_spectrum(body: SpectrumIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"batch:{body.batch_id}"
    with connect() as conn:
        # 同批次并发导出抢批次锁：后到只记争用痕，不产正式行/文件
        try:
            acquired = try_acquire(conn, lock_key, user)
        except sqlite3.IntegrityError:
            # 并发首插冲突：争用痕独立成笔后拒收
            conn.rollback()
            conn.execute(
                """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
                   VALUES (?,?,?,?,?)""",
                (lock_key, user, "争用", "并发抢锁首插冲突", now_iso()),
            )
            conn.commit()
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        if not acquired:
            conn.commit()
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        try:
            batch = conn.execute(
                "SELECT * FROM temp_batches WHERE id=?", (body.batch_id,)
            ).fetchone()
            if not batch:
                raise HTTPException(404, "批次不存在")
            if batch["status"] != "已封批":
                raise HTTPException(409, "须先封批再导出温谱")
            current_gen = int(batch["generation"])
            latest_seal = conn.execute(
                """SELECT batch_generation FROM seal_snapshots
                   WHERE batch_id=? ORDER BY id DESC LIMIT 1""",
                (body.batch_id,),
            ).fetchone()
            if not latest_seal:
                raise HTTPException(409, "封批快照缺失，禁止导出温谱")
            seal_gen = int(latest_seal["batch_generation"])
            # 甲路径硬校验：请求代必须等于当前封批代（与最新封批快照一致），
            # 落后（旧代冒充）或超前（封批代落后于请求）一律拒收，正式表零增
            if body.generation != current_gen or body.generation != seal_gen:
                write_audit(
                    conn,
                    user,
                    "spectrum_export_rejected",
                    batch["batch_code"],
                    {
                        "request_generation": body.generation,
                        "current_generation": current_gen,
                        "seal_generation": seal_gen,
                        "reason": "generation_mismatch",
                    },
                )
                conn.commit()
                raise HTTPException(
                    409,
                    f"封批代校验失败：请求代G{body.generation}"
                    f"≠当前封批代G{current_gen}，温谱正式行零增拒收",
                )
            points = json.loads(batch["temp_points"] or "[]")
            payload = json.dumps(
                {
                    "batch_code": batch["batch_code"],
                    "generation": current_gen,
                    "points": points,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
            ref = f"SP-{batch['batch_code']}-G{current_gen}"
            dup = conn.execute(
                "SELECT id FROM spectrum_exports WHERE export_ref=?", (ref,)
            ).fetchone()
            if dup:
                # 后到请求（胜者已完成并释放锁）：同代正式温谱已存在，
                # 按争用落败处理，只写争用痕，不产文件/正式行
                conn.execute(
                    """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
                       VALUES (?,?,?,?,?)""",
                    (lock_key, user, "争用", f"同代温谱已存在={ref}", now_iso()),
                )
                write_audit(
                    conn,
                    user,
                    "spectrum_export_contention",
                    ref,
                    {"reason": "duplicate_generation_export", "generation": current_gen},
                )
                conn.commit()
                raise HTTPException(409, "争用：该代温谱已导出，后到只记争用不产文件")
            cur = conn.execute(
                """INSERT INTO spectrum_exports(batch_id,export_ref,point_count,checksum,
                   status,generation,created_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    body.batch_id,
                    ref,
                    len(points),
                    checksum,
                    "已导出",
                    current_gen,
                    now_iso(),
                ),
            )
            write_audit(
                conn,
                user,
                "spectrum_export",
                ref,
                {"checksum": checksum, "generation": current_gen},
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "export_ref": ref,
                "point_count": len(points),
                "checksum": checksum,
                "generation": current_gen,
                "status": "已导出",
            }
        finally:
            # 异常退出时上下文管理器会回滚，故释放后须显式提交，避免锁残留
            release(conn, lock_key, user)
            conn.commit()


@router.get("/api/spectrum/{export_ref}")
def get_export(export_ref: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, b.temp_points, b.batch_code FROM spectrum_exports s
               JOIN temp_batches b ON b.id=s.batch_id WHERE s.export_ref=?""",
            (export_ref,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "导出不存在")
        data = dict(row)
        data["temp_points"] = json.loads(data["temp_points"] or "[]")
        return data
