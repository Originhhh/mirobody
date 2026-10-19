"""Synthetic write/read regressions executed against PostgreSQL, including CI.

Set MIROBODY_TEST_POSTGRES_DSN to a disposable PostgreSQL database. Every test
creates and drops its own schema; no application account or document is read.
"""

from __future__ import annotations

import argparse
import importlib
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from .test_collect_regressions import _EXPORT_COLLISIONS

DSN = os.environ.get("MIROBODY_TEST_POSTGRES_DSN", "")
pytestmark = [pytest.mark.needs_db, pytest.mark.skipif(not DSN, reason="MIROBODY_TEST_POSTGRES_DSN is not set")]


@pytest.fixture
async def database(monkeypatch):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from mirobody.collect import observations
    from mirobody.task import ProfileRefreshTask
    from mirobody.utils import db

    schema = "regression_" + uuid4().hex
    driver_url = make_url(DSN).set(drivername="postgresql")
    connection = await psycopg.AsyncConnection.connect(driver_url.render_as_string(hide_password=False), autocommit=True)
    engine = None
    try:
        await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        # Identity functions keep these synthetic SQL fixtures independent of
        # a deployment's encryption key. Encryption has its own security gate.
        await connection.execute("CREATE FUNCTION encrypt_content(text) RETURNS text LANGUAGE SQL IMMUTABLE AS $$SELECT nullif($1,'')$$")
        await connection.execute("CREATE FUNCTION decrypt_content(text) RETURNS text LANGUAGE SQL IMMUTABLE AS $$SELECT $1$$")
        root = Path(__file__).resolve().parents[1]
        for file in ("10_accounts.sql", "20_files.sql", "30_observations.sql", "31_medications.sql", "32_genomics.sql",
                     "40_devices.sql", "41_device_rules.sql"):
            await connection.execute((root / "schema" / file).read_text())
        await connection.execute("INSERT INTO health_app_user(is_del,email,tz) VALUES (false,'synthetic@example.invalid','UTC')")
        chat = (root / "schema" / "50_chat.sql").read_text()
        await connection.execute(chat[chat.index("CREATE TABLE IF NOT EXISTS th_chat_citation ("):])
        engine = create_async_engine(make_url(DSN).set(drivername="postgresql+psycopg"),
                                     connect_args={"options": f"-c search_path={schema} -c timezone=UTC"})
        monkeypatch.setattr(db, "_provider", lambda _: engine)
        monkeypatch.setattr(db, "_engines", {})
        monkeypatch.setattr(observations, "_range_rules", None)
        monkeypatch.setattr(ProfileRefreshTask, "enqueue", AsyncMock())
        yield db.execute_query
    finally:
        if engine is not None:
            await engine.dispose()
        await connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        await connection.close()


async def _write_glucose():
    from mirobody.collect import observations

    return await observations.ingest("1", [
        observations.Draft(name_text="FPG", value_text="4.76", note_text="synthetic printed timing context",
                           observed_start=datetime(2026, 1, 1, tzinfo=UTC)),
        observations.Draft(name_text="FPG", value_text="6.0", unit_text="mmol/L", observed_start=datetime(2026, 1, 2, tzinfo=UTC)),
    ], observations.Provenance(modality="lab-report", source_kind="file", source_ref="th_files:synthetic-glucose"), user_tz="UTC")


async def test_confirmation_without_unit_does_not_silently_join_a_standard_series(database):
    router = importlib.import_module("mirobody.server.routers.indicator_router")
    report = await _write_glucose()
    assert report.inserted == 2
    response = await router.confirm_code(router.CodeConfirmation(id=report.ids[0], code="14771-0"), "1")
    assert response.code == 400
    rows = await database("SELECT outcome, series_id FROM v_observation WHERE id=:id", {"id": report.ids[0]}, log_sql=False)
    assert rows[0]["outcome"] == "needs-input"
    assert rows[0]["series_id"].startswith("local:")
    assert not await database("SELECT id FROM th_coding_alias", log_sql=False)


async def test_confirmation_http_does_not_write_another_persons_record(database):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    router = importlib.import_module("mirobody.server.routers.indicator_router")
    report = await _write_glucose()
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[router.verify_token] = lambda: "2"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/health-indicators/confirm-code",
                                     json={"id": report.ids[0], "code": "14771-0", "unit": "mmol/L"})
    assert response.json()["code"] == 404
    assert not await database("SELECT id FROM th_coding_alias", log_sql=False)
    count = await database("SELECT count(*) AS n FROM th_observation", log_sql=False)
    assert count[0]["n"] == 2


async def test_confirmed_unit_preserves_original_and_both_readings_enter_statistics(database):
    from mirobody.collect import observations
    from mirobody.collect.query import PostgresHealthQuery
    from mirobody.kernel import query

    router = importlib.import_module("mirobody.server.routers.indicator_router")
    report = await _write_glucose()
    response = await router.confirm_code(router.CodeConfirmation(id=report.ids[0], code="14771-0", unit="mmol/L"), "1")
    assert response.code == 0
    assert response.data["id"] != report.ids[0]
    stored = await database("SELECT unit_text FROM th_observation WHERE id=:id", {"id": report.ids[0]}, log_sql=False)
    assert stored[0]["unit_text"] == ""
    rows = await database("SELECT outcome, value_canonical, unit_canonical, decrypt_content(note_text) AS note "
                          "FROM v_observation ORDER BY observed_start", log_sql=False)
    assert len(rows) == 2
    assert all(r["outcome"] == "coded" and r["value_canonical"] is not None for r in rows)
    assert float(rows[0]["value_canonical"]) == pytest.approx(0.00476)
    assert rows[0]["note"] == "synthetic printed timing context"
    stats = await PostgresHealthQuery().stats("1", query.Selection(indicators=("14771-0",)), query.Window(0, 0, "UTC"))
    assert stats[0]["count"] == stats[0]["numeric_count"] == 2
    assert stats[0]["avg"] == pytest.approx(5.38)
    later = await observations.ingest("1", [observations.Draft(name_text="FPG", value_text="5.1",
        observed_start=datetime(2026, 1, 3, tzinfo=UTC))],
        observations.Provenance(modality="lab-report", source_kind="file", source_ref="th_files:synthetic-later"), user_tz="UTC")
    rows = await database("SELECT outcome, series_id FROM v_observation WHERE id=:id", {"id": later.ids[0]}, log_sql=False)
    assert rows[0]["outcome"] == "needs-input"
    assert rows[0]["series_id"].startswith("local:")


async def test_a_mixed_unit_mean_keeps_its_precision_and_a_single_basis(database):
    from mirobody.collect import observations
    from mirobody.collect.query import PostgresHealthQuery
    from mirobody.kernel import query

    drafts = [observations.Draft(name_text=name, value_text=value, unit_text=unit,
                                 observed_start=datetime(2026, 1, day, tzinfo=UTC))
              for name, value, unit, day in (("Creatinine", "80", "umol/L", 1), ("Creatinine", "0.9", "mg/dL", 2),
                                             ("Albumin", "40", "g/L", 1), ("Albumin", "600", "umol/L", 2))]
    await observations.ingest("1", drafts, observations.Provenance(modality="lab-report", source_kind="file",
                                                                   source_ref="th_files:synthetic-units"), user_tz="UTC")
    store, window = PostgresHealthQuery(), query.Window(0, 0, "UTC")
    creatinine, albumin = query.Selection(indicators=("2160-0",)), query.Selection(indicators=("1751-7",))
    # 0.9 mg/dL is 79.56 umol/L; rounded to four places in mol/L the mean read 0.0001.
    [stats] = await store.stats("1", creatinine, window)
    [month] = await store.buckets("1", creatinine, window, resolution="month", limit=10)
    for row in (stats, month):
        assert row["unit"] == "mol/L"
        assert row["avg"] == pytest.approx(79.7808e-6, rel=1e-5)
    # No molar mass joins g/L to mol/L, so there is no mean to give.
    [stats] = await store.stats("1", albumin, window)
    [month] = await store.buckets("1", albumin, window, resolution="month", limit=10)
    assert stats["count"] == 2 and stats["mixed_units"]
    for row in (stats, month):
        assert (row["min"], row["max"], row["avg"], row["unit"]) == (None, None, None, "")


async def test_confirmation_does_not_replace_an_existing_recognized_unit(database):
    from mirobody.collect import observations

    report = await observations.ingest("1", [observations.Draft(name_text="SyntheticUnlistedMarker",
        value_text="4.76", unit_text="mg/dL", observed_start=datetime(2026, 1, 1, tzinfo=UTC))],
        observations.Provenance(modality="lab-report", source_kind="file", source_ref="th_files:synthetic-unlisted"), user_tz="UTC")
    router = importlib.import_module("mirobody.server.routers.indicator_router")
    response = await router.confirm_code(router.CodeConfirmation(id=report.ids[0], code="14771-0", unit="mmol/L"), "1")
    assert response.code == 400
    rows = await database("SELECT id, unit_text FROM v_observation", log_sql=False)
    assert rows == [{"id": report.ids[0], "unit_text": "mg/dL"}]
    assert not await database("SELECT id FROM th_coding_alias", log_sql=False)


async def test_amendment_preserves_notes_unless_a_new_note_is_requested(database):
    from mirobody.collect import observations

    report = await _write_glucose()
    original = report.ids[0]
    kept = await observations.amend("1", original, unit_text="mmol/L")
    rows = await database("SELECT decrypt_content(note_text) AS note FROM v_observation WHERE id=:id",
                          {"id": kept}, log_sql=False)
    assert rows[0]["note"] == "synthetic printed timing context"
    cleared = await observations.amend("1", kept, note="")
    assert cleared != kept
    rows = await database("SELECT decrypt_content(note_text) AS note FROM v_observation WHERE id=:id",
                          {"id": cleared}, log_sql=False)
    assert rows[0]["note"] is None


@pytest.mark.parametrize("source_order", [("Apple Watch", "iPhone"), ("iPhone", "Apple Watch")])
@pytest.mark.parametrize("same_instant", [False, True], ids=["overlapping", "same-instant"])
async def test_apple_import_selects_one_device_before_cumulative_aggregation(database, monkeypatch, tmp_path, source_order, same_instant):
    from mirobody import cli
    from mirobody.collect.query import PostgresHealthQuery
    from mirobody.utils.config import Config

    records = []
    for second, source in enumerate(source_order):
        second = 0 if same_instant else second
        number = 100 if source == "Apple Watch" else 80
        records.append(f'<Record type="HKQuantityTypeIdentifierStepCount" sourceName="{source}" unit="count" '
                       f'startDate="2026-01-01 10:00:0{second} +0000" endDate="2026-01-01 10:10:0{second} +0000" value="{number}"/>')
    export = tmp_path / "synthetic-export.xml"
    export.write_text("<HealthData>" + "".join(records) + "</HealthData>")
    monkeypatch.setattr(Config, "init", AsyncMock())
    args = argparse.Namespace(file=str(export), into="1", config=[], tz="UTC")
    first = await cli._store_export(args)
    assert first["records"] == first["facts"] == 2
    assert first["added"] > 0
    from mirobody.translate.aggregate.aggregators.sql_aggregator import SQLAggregator
    sums = await SQLAggregator()._execute_single_sql_aggregation(["1"], ["steps"], datetime(2026, 1, 1), "UTC", {"sum"})
    assert float(sums[0]["sum_value"]) == 100
    if same_instant:
        points = await database("SELECT source_id FROM series_data", log_sql=False)
        assert points == [{"source_id": "Apple Watch"}]
    second = await cli._store_export(args)
    assert second["added"] == 0
    records = await PostgresHealthQuery().catalog("1", None)
    assert any(float(r["latest_value"]) == 100 for r in records if "step" in r["indicator"].lower())


async def test_vcf_loc_and_rsid_citations_resolve_with_ownership_and_upload_lifecycle(database, tmp_path):
    from mirobody.agent.tool_loader import _envelope_wrapper
    from mirobody.agent.tools.genetic_service import GeneticService
    from mirobody.collect import citations
    from mirobody.collect.files.services import genotype_format
    from mirobody.collect.files.services.genetic_processor import GeneticDataLoader
    from mirobody.translate.genotype import normalize

    vcf = '##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSYNTHETIC\n1\t123456\t.\tA\tG\t.\tPASS\t.\tGT\t0/1\n'
    file = tmp_path / "synthetic.vcf"
    file.write_text(vcf)
    record = next(GeneticDataLoader().parse_genetic_file(str(file), genotype_format.sniff(vcf)))
    call = normalize(rsid=record["rsid"], chrom=record["chromosome"], position=record["position"],
                     genotype=record["genotype"], strand=record["strand"], vcf_gt=record["gt"],
                     vcf_ref=record["ref"], vcf_alt=record["alt"])
    assert call.rsid.startswith("loc:")
    await database("INSERT INTO th_files(user_id,file_name,file_key,created_source) VALUES ('1','synthetic.vcf','synthetic.vcf','test')", log_sql=False)
    sets = await database("""INSERT INTO th_genotype_set(user_id,file_key,format_id,vendor,status,n_rows,n_called,
        normalizer_version,site_table_version,activated_at) VALUES ('1','synthetic.vcf','vcf','synthetic','active',2,2,'test','test',now()) RETURNING id""", log_sql=False)
    set_id = sets[0]["id"]
    for identity, position in ((call.rsid, 123456), ("rs1000000001", 123457)):
        await database("""INSERT INTO th_genotype(set_id,rsid,rsid_raw,chrom,position_raw,ref,alt,genotype_raw,gt,
            call_status,zygosity,strand_check) VALUES (:set,:id,'.',:chrom,:pos,:ref,:alt,:raw,:gt,:status,:zygosity,:strand)""",
            {"set":set_id,"id":identity,"chrom":call.chrom,"pos":position,"ref":call.ref,"alt":call.alt,
             "raw":record["genotype_raw"],"gt":call.gt,"status":call.call_status,"zygosity":call.zygosity,"strand":call.strand_check}, log_sql=False)
    service = GeneticService()
    rendered, envelope = await _envelope_wrapper(service.query_genetic_data, {"user_id":"1","session_id":"synthetic-thread"})(
        chromosome="1", start=123456, end=123457, build="raw")
    rids = [row["rid"] for row in envelope.data]
    assert len(rids) == 2 and all(rid in rendered for rid in rids)
    resolved = await citations.resolve_rids("synthetic-thread", "1", rids)
    assert all(r["status"] == "ok" and r["kind"] == "genotype" and r["file"] == "synthetic.vcf" for r in resolved)
    assert all(r["status"] == "unknown" for r in await citations.resolve_rids("synthetic-thread", "2", rids))
    await database("UPDATE th_genotype_set SET status='superseded' WHERE id=:id", {"id":set_id}, log_sql=False)
    assert all(r["status"] == "gone" for r in await citations.resolve_rids("synthetic-thread", "1", rids))


async def test_health_data_e2e_script_runs_over_synthetic_rows(database, monkeypatch, caplog):
    from mirobody.collect import observations
    from mirobody.server.demo import PHI_CANARY
    from mirobody.utils.config import Config

    report = await observations.ingest("1", [
        observations.Draft(name_text="RestingHeartRate-RHR", value_text="60", unit_text="/min",
                           note_text=PHI_CANARY, observed_start=datetime(2025, 6, 1, tzinfo=UTC)),
        observations.Draft(name_text="RestingHeartRate-RHR", value_text="62", unit_text="/min",
                           observed_start=datetime(2025, 6, 2, tzinfo=UTC)),
    ], observations.Provenance(modality="lab-report", source_kind="file", source_ref="th_files:synthetic-e2e"), user_tz="UTC")
    assert report.inserted == 2
    script = Path(__file__).resolve().parents[2] / "scripts" / "e2e_health_data.py"
    spec = importlib.util.spec_from_file_location("health_data_e2e_gate", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(Config, "init", AsyncMock())
    assert await module.run("1", None) == 0
    assert PHI_CANARY not in caplog.text


@pytest.mark.parametrize('left,right,expected', _EXPORT_COLLISIONS)
@pytest.mark.parametrize('reverse', [False,True])
@pytest.mark.parametrize('batch_size', [1,10000])
async def test_export_collisions_execute_on_postgres(database,monkeypatch,left,right,expected,reverse,batch_size):
    repository = importlib.import_module('mirobody.collect.ingest.repositories.health_data')
    monkeypatch.setattr(repository,'_BATCH_SIZE',batch_size)
    readings = [left,right][::-1] if reverse else [left,right]
    records = [{'user_id':'1','indicator':'steps','source':'apple_health_export','start_time':datetime(2026,1,1,10),'value':value,'timezone':'UTC','source_id':name,'task_id':task} for name,value,task in readings]
    assert await repository.HealthDataRepository().save_health_records(records)
    rows = await database('SELECT source_id,value,task_id FROM series_data',log_sql=False)
    assert (rows[0]['source_id'],rows[0]['value'],rows[0]['task_id']) == expected

@pytest.mark.parametrize('source', ['apple_health_export','apple_health','vital.synthetic'])
async def test_device_updates_still_apply_on_postgres(database,source):
    repository = importlib.import_module('mirobody.collect.ingest.repositories.health_data')
    record={'user_id':'1','indicator':'steps','source':source,'start_time':datetime(2026,1,1,10),'value':'100','timezone':'UTC','source_id':'Apple Watch'}
    writer=repository.HealthDataRepository()
    assert await writer.save_health_records([record])
    assert await writer.save_health_records([{**record,'value':'120','task_id':'synthetic-repair'}])
    rows=await database('SELECT source_id,value,task_id FROM series_data',log_sql=False)
    assert (rows[0]['source_id'],rows[0]['value'],rows[0]['task_id']) == ('Apple Watch','120','synthetic-repair')

@pytest.mark.parametrize('old_task,new_task', [(None,'filtered_out_of_range'),('filtered_out_of_range',None)])
async def test_same_export_device_can_correct_range_status_postgres(database,old_task,new_task):
    repository = importlib.import_module('mirobody.collect.ingest.repositories.health_data')
    record={'user_id':'1','indicator':'steps','source':'apple_health_export','start_time':datetime(2026,1,1,10),'value':'100','timezone':'UTC','source_id':'Apple Watch','task_id':old_task}
    writer=repository.HealthDataRepository()
    assert await writer.save_health_records([record])
    assert await writer.save_health_records([{**record,'task_id':new_task}])
    rows=await database('SELECT source_id,value,task_id FROM series_data',log_sql=False)
    assert (rows[0]['source_id'],rows[0]['value'],rows[0]['task_id']) == ('Apple Watch','100',new_task)


_EVIDENCE_CASES = {
    "unverified": [("2026-01-01T09:00", "llm-unverified", False)],
    "verified": [("2026-01-01T09:00", "lab-report", False)],
    "unknown": [("2026-01-01T09:00", "", False)],
    "mixed": [("2026-01-01T09:00", "llm-unverified", False), ("2026-01-02T09:00", "lab-report", False)],
    "window": [("2026-01-01T09:00", "llm-unverified", False), ("2026-01-02T09:00", "lab-report", False)],
    "elected-verified": [("2026-01-01T09:00", "lab-report", True), ("2026-01-01T10:00", "llm-unverified", False)],
    "latest-verified": [("2026-01-01T09:00", "llm-unverified", False), ("2026-01-01T10:00", "lab-report", False)],
}


@pytest.mark.parametrize("case", list(_EVIDENCE_CASES))
@pytest.mark.parametrize("view", ["catalog", "stats", "raw", "latest", "minute", "hour", "day", "week", "month"])
async def test_evidence_survives_actual_writes_windows_and_elected_answers(database, case, view):
    from mirobody.agent.tools.health_indicators_service import _envelope_for
    from mirobody.collect import observations
    from mirobody.collect.files.services.indicator_store import save_indicators_to_db
    from mirobody.collect.query import PostgresHealthQuery
    from mirobody.kernel import query

    for index, (day, modality, elected) in enumerate(_EVIDENCE_CASES[case]):
        at = datetime.fromisoformat(day).replace(tzinfo=UTC)
        value = 33 + index
        if modality:
            text = f"ALT {value + 10 if modality == 'llm-unverified' else value} U/L"
            report = await save_indicators_to_db("1", [{"original_indicator":"ALT", "value":str(value),
                "unit":"U/L", "detection_method":"laboratory"}], at, "extracted", f"synthetic-evidence-{index}", text=text)
        else:
            report = await observations.ingest("1", [observations.Draft(name_text="ALT", value_text=str(value),
                unit_text="U/L", observed_start=at)], observations.Provenance(modality="", source_kind="file",
                source_ref=f"th_files:synthetic-evidence-{index}"), user_tz="UTC")
        assert report.inserted == 1
        stored = await database("SELECT modality, series_id, local_date FROM v_observation WHERE id=:id",
                                {"id": report.ids[0]}, log_sql=False)
        assert stored[0]["modality"] == modality
        if elected:
            await database("INSERT INTO th_day_authority(user_id,series_id,local_date,observation_id,rule,candidates) "
                           "VALUES ('1',:series,:day,:id,'synthetic-election',2)",
                           {"series":stored[0]["series_id"],"day":stored[0]["local_date"],"id":report.ids[0]}, log_sql=False)

    reader = PostgresHealthQuery()
    selection = query.Selection(indicators=("ALT",))
    window = query.resolve_window("UTC", start="2026-01-02", end="2026-01-02", hours=24,
        now=datetime(2026, 10, 14, tzinfo=UTC)) if case == "window" else query.Window(0, 0, "UTC")
    if view == "catalog":
        method, rows = "catalog", await reader.catalog("1", window)
    elif view == "raw":
        method, rows = "readings", await reader.readings("1", selection, window, limit=30)
    elif view in ("stats", "latest"):
        method, rows = view, await getattr(reader, view)("1", selection, window)
    else:
        method, rows = "buckets", await reader.buckets("1", selection, window, resolution=view, limit=30)
    assert rows
    expected = case not in ("verified", "unknown", "window")
    if case == "mixed" and view == "latest":
        expected = False
    if case == "elected-verified" and view in ("stats", "latest", "day", "week", "month"):
        expected = False
    if case == "latest-verified" and view in ("latest", "day", "week", "month"):
        expected = False
    if view not in ("raw", "latest"):
        assert any(r["unverified"] for r in rows) is expected
    request = query.QueryRequest(selection=selection, view="raw" if view == "catalog" else view,
                                 start=window.start if case == "window" else "", end=window.end if case == "window" else "")
    envelope = _envelope_for(method, request, window, rows)
    assert any("check the file" in note for note in envelope.assumptions) is expected
