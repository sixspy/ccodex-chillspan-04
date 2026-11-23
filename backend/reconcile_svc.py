"""仓间配额 vs 在批差额对账 + 互证只读。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from db import connect

router = APIRouter(tags=["reconcile"])


def _chamber_recon(conn, chamber) -> dict:
    cid = chamber["id"]
    quota = int(chamber["quota_batches"])
    active = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches
           WHERE chamber_id=? AND status NOT IN ('已封批','作废')""",
        (cid,),
    ).fetchone()["c"]
    sealed = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches
           WHERE chamber_id=? AND status='已封批'""",
        (cid,),
    ).fetchone()["c"]
    gap = quota - active
    ok = active <= quota
    return {
        "chamber_id": cid,
        "chamber_code": chamber["chamber_code"],
        "region": chamber["region"],
        "quota_batches": quota,
        "active_batches": active,
        "sealed_batches": sealed,
        "quota_left": gap,
        "balanced": ok,
        "proof": {
            "formula": "active_batches <= quota_batches",
            "left": active,
            "right": quota,
        },
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        chambers = conn.execute("SELECT * FROM cold_chambers ORDER BY id").fetchall()
        items = [_chamber_recon(conn, c) for c in chambers]
        bad = [i for i in items if not i["balanced"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
            },
        }


@router.get("/api/reconcile/{chamber_code}")
def reconcile_one(chamber_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        ch = conn.execute(
            "SELECT * FROM cold_chambers WHERE chamber_code=?", (chamber_code,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "仓间不存在")
        return _chamber_recon(conn, ch)


@router.get("/api/proof/batch/{batch_code}")
def batch_proof(batch_code: str, authorization: AuthHeader = None):
    """批次互证：封批代 + 温谱导出 + 开放游标。"""
    require_user(authorization)
    with connect() as conn:
        batch = conn.execute(
            "SELECT * FROM temp_batches WHERE batch_code=?", (batch_code,)
        ).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        open_exc = conn.execute(
            """SELECT COUNT(*) c FROM excursion_cursors
               WHERE batch_id=? AND status='开放'""",
            (batch["id"],),
        ).fetchone()["c"]
        exports_rows = conn.execute(
            """SELECT export_ref, generation FROM spectrum_exports
               WHERE batch_id=? AND status='已导出'""",
            (batch["id"],),
        ).fetchall()
        exports = len(exports_rows)
        # 五页同态口径：互证逐行核对导出代 == 当前封批代，错代即不一致，
        # 不再按条数凑一致
        export_gens = sorted(
            {int(r["generation"]) for r in exports_rows if r["generation"] is not None}
        )
        sealed = batch["status"] == "已封批"
        current_gen = int(batch["generation"])
        latest_seal = conn.execute(
            """SELECT batch_generation FROM seal_snapshots
               WHERE batch_id=? ORDER BY id DESC LIMIT 1""",
            (batch["id"],),
        ).fetchone()
        seal_gen = int(latest_seal["batch_generation"]) if latest_seal else None
        if sealed:
            spectrum_ok = (
                exports >= 1
                and bool(export_gens)
                and all(g == current_gen for g in export_gens)
                and (seal_gen is None or seal_gen == current_gen)
            )
        else:
            spectrum_ok = True
        return {
            "batch_code": batch_code,
            "status": batch["status"],
            "generation": current_gen,
            "seal_generation": seal_gen,
            "open_excursions": open_exc,
            "spectrum_exports": exports,
            "spectrum_generations": export_gens,
            "sealed": sealed,
            "excursion_closed": open_exc == 0,
            "spectrum_matches_seal": spectrum_ok,
            "proof": {
                "formula": "sealed => spectrum>=1 and every export_generation==current_generation",
                "sealed": sealed,
                "open_excursions": open_exc,
                "exports": exports,
                "current_generation": current_gen,
                "spectrum_generations": export_gens,
            },
        }