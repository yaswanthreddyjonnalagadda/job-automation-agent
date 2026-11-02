"""The multinational employers the agent scans come from data, read by one function, and are what the owner asked for (f299).

The owner, 2 November 2026: add multinational companies that sponsor H-1B visas and have offices in India, as many as
possible, and no consultancies. The list is data (reference/employers.json); each address was tested live when it was
written. Whether a company sponsors a visa is read from each posting by the scan's own checks, not assumed here.
"""
import json
from pathlib import Path

import pytest

import discovery_sources as ds

FILE = Path(__file__).resolve().parent.parent / "reference" / "employers.json"

# IT-services, consulting, staffing and government-contracting firms: not what the owner asked for. A name here must
# never be in the list, whatever system it uses. (This is test data, not a rule the agent applies.)
NOT_WANTED = {
    "accenture", "infosys", "tata consultancy", "tcs", "wipro", "cognizant", "capgemini", "hcl", "hcltech", "tech mahindra",
    "ltimindtree", "mindtree", "mphasis", "genpact", "deloitte", "pwc", "pricewaterhouse", "ernst", "kpmg", "mckinsey",
    "bain", "boston consulting", "booz allen", "leidos", "saic", "caci", "gdit", "mantech", "peraton", "thoughtworks",
    "epam", "globant", "persistent", "coforge", "hexaware", "zensar", "cybage", "virtusa", "robert half", "randstad",
    "kforce", "insight global", "teksystems", "apex systems", "ibm consulting", "kyndryl",
}


def employers():
    return json.loads(FILE.read_text(encoding="utf-8"))["employers"]


def test_the_list_is_a_real_list_of_employers():
    rows = employers()
    assert len(rows) >= 50
    for row in rows:
        assert row["company"].strip() and row["ats"] in {"greenhouse", "lever", "ashby", "workday", "icims"}, row
        if row["ats"] == "workday":
            assert {"host", "tenant", "site"} <= set(row) and row["host"].endswith(".myworkdayjobs.com"), row
            assert row["host"].startswith(row["tenant"] + "."), row
        else:
            assert row.get("token") or row.get("host"), row


def test_no_employer_is_listed_twice():
    keys = [ds._identity(row) for row in employers()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("row", employers(), ids=lambda r: r["company"])
def test_no_consultancy_or_services_firm_is_in_the_list(row):
    name = row["company"].lower()
    assert not any(bad in name for bad in NOT_WANTED), f"{row['company']} is not a multinational product employer"


def test_the_list_is_read_by_one_function_and_every_row_is_a_scannable_entry():
    rows = ds.reference_employers()
    assert len(rows) == len(employers())
    assert all(ds._identity(row)[1] for row in rows)


def test_the_owners_own_list_comes_first_then_the_reference_then_the_history(tmp_path):
    listed = [{"company": "Mine", "token": "mine", "ats": "greenhouse"}]
    reference = [{"company": "Mine (again)", "token": "mine", "ats": "greenhouse"},
                 {"company": "Big Co", "ats": "workday", "host": "bigco.wd5.myworkdayjobs.com", "tenant": "bigco", "site": "X"}]
    history = [{"company": "Big Co (history)", "ats": "workday", "host": "bigco.wd5.myworkdayjobs.com", "tenant": "bigco", "site": "X"},
               {"company": "Old", "token": "old", "ats": "lever"}]
    merged = ds.merge_companies(listed, reference, history)
    assert [row["company"] for row in merged] == ["Mine", "Big Co", "Old"]      # each once; the first to name it wins


def test_the_engine_scans_the_reference_employers_too(tmp_path, monkeypatch):
    import job_discovery as jd
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Mine", "token": "mine", "ats": "greenhouse"}]), encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    engine.history = ()
    names = {row["company"] for row in engine.get_target_companies()}
    assert "Mine" in names and "Cisco" in names and "NVIDIA" in names
