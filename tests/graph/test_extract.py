"""Offline tests for graph extraction: resolution, ontology validation, grounding, PII masking."""
import pytest

from ingestion.chunk import Chunk
from ingestion.extract_graph import (
    Entity,
    Extraction,
    Known,
    Relation,
    Resolver,
    exact_mentions,
    mask_pii,
    validate,
)
from ingestion.index import load_chunk_entities
from ingestion.metadata import DocMetadata


@pytest.fixture
def known():
    return Known(
        names={"System": ["Auth Service", "Kafka Cluster", "Ledger DB", "Payment Gateway"],
               "Team": ["Data-Platform"], "Person": ["Anthony Gonzalez"], "Product": ["Acme Pay"]},
        docs={"INC-2025-031": {"title": "Payment latency spike", "doc_type": "incident"},
              "POL-COMP-001": {"title": "Data Retention Policy", "doc_type": "policy"}},
        backbone={("Payment Gateway", "DEPENDS_ON", "Ledger DB"), ("INC-2025-031", "AFFECTS", "Ledger DB")},
    )


def chunk(text: str, doc_id: str = "MAN-ENG-001", doc_type: str = "manual") -> Chunk:
    meta = DocMetadata(doc_id=doc_id, title="t", doc_type=doc_type, domain="engineering", owner=None,
                       version=None, effective_date=None, sensitivity="internal", source_path="x",
                       page=None, section=None)
    return Chunk(chunk_id=f"{doc_id}::000", doc_id=doc_id, chunk_index=0, text=text, metadata=meta)


def rel(src, sl, r, dst, dl, ev):
    return Relation(src=src, src_label=sl, rel=r, dst=dst, dst_label=dl, evidence=ev)


def test_resolver_exact_fuzzy_alias_and_drop(known):
    r = Resolver(known)
    assert r.resolve("payment gateway", "System") == ("System", "Payment Gateway")
    assert r.resolve("Ledger DBs", "System") == ("System", "Ledger DB")           # fuzzy >= 90
    assert r.resolve("Kafka", "Component") == ("System", "Kafka Cluster")         # short form of a system
    assert r.resolve("Jane Unknown", "Person") is None                            # people are never created
    assert r.resolve("Data Platform Team", "Team") is None                        # below the threshold
    assert r.resolve("POL-COMP-001", "Policy") == ("Policy", "POL-COMP-001")
    assert r.resolve("INC-2025-031", "Policy") is None                            # wrong document type


def test_resolver_components_created_on_demand_and_deduped(known):
    r = Resolver(known)
    assert r.resolve("Schema Registry", "Component") is None
    created = r.resolve("Schema Registry", "Component", create_component=True)
    assert created == ("Component", "Schema Registry")
    assert r.resolve("schema registry", "Component") == ("Component", "Schema Registry")
    assert r.resolve("disk space issues", "Component", create_component=True) is None  # generic


def test_validate_statuses_grounding_and_ontology(known):
    text = ("The Payment Gateway consists of: - **Tokenization Service**: tokens. "
            "The Payment Gateway depends on the Ledger DB and on Auth Service.")
    ex = Extraction(
        entities=[Entity(name="Payment Gateway", label="System", type=None),
                  Entity(name="Anthony Gonzalez", label="Person", type=None)],  # not in the text
        relations=[
            rel("Payment Gateway", "System", "HAS_COMPONENT", "Tokenization Service", "Component",
                "The Payment Gateway consists of: Tokenization Service"),        # non-adjacent quote is ok
            rel("Payment Gateway", "System", "DEPENDS_ON", "Ledger DB", "System",
                "The Payment Gateway depends on the Ledger DB"),
            rel("Payment Gateway", "System", "DEPENDS_ON", "Auth Service", "System",
                "depends on the Ledger DB and on Auth Service"),
            rel("Data-Platform", "Team", "OWNS", "Payment Gateway", "System", "Data-Platform owns it"),
            rel("Payment Gateway", "System", "GOVERNS", "Ledger DB", "System", "depends on the Ledger DB"),
        ])
    v = validate(ex, chunk(text), Resolver(known))
    got = {(r["src"], r["rel"], r["dst"]): r["status"] for r in v.relations}
    assert got == {("Payment Gateway", "HAS_COMPONENT", "Tokenization Service"): "new",
                   ("Payment Gateway", "DEPENDS_ON", "Ledger DB"): "backbone",
                   ("Payment Gateway", "DEPENDS_ON", "Auth Service"): "conflict"}
    assert v.rejected_relations == 2  # ungrounded OWNS + GOVERNS outside the ontology
    assert ("Person", "Anthony Gonzalez") not in v.mentions and v.dropped_entities == 1
    assert ("Component", "Tokenization Service") in v.mentions
    assert {(r["source_doc_id"], r["chunk_id"]) for r in v.relations} == {("MAN-ENG-001", "MAN-ENG-001::000")}


def test_incident_relations_use_doc_ids(known):
    c = chunk("Ledger DB connection pool exhaustion caused the latency.", "INC-2025-031", "incident")
    ex = Extraction(entities=[], relations=[
        rel("INC-2025-031", "Incident", "AFFECTS", "Ledger DB", "System", "Ledger DB connection pool"),
        rel("INC-2025-099", "Incident", "AFFECTS", "Ledger DB", "System", "Ledger DB connection pool"),
    ])
    v = validate(ex, c, Resolver(known))
    assert [(r["src"], r["status"]) for r in v.relations] == [("INC-2025-031", "backbone")]


def test_exact_mentions_word_boundaries(known):
    assert exact_mentions("Restart the payment gateway, then Acme Pay.", known) == [
        ("System", "Payment Gateway"), ("Product", "Acme Pay")]
    assert exact_mentions("Ledger DBX is unrelated", known) == []


def test_mask_pii_in_evidence():
    s = mask_pii("call (827) 313-0730 or mail a.b@x.com from 76.168.253.56, SSN 709-48-8434")
    assert "313" not in s and "@" not in s and "76.168" not in s and "8434" not in s


def test_load_chunk_entities(tmp_path):
    p = tmp_path / "mentions.csv"
    p.write_text("chunk_id,doc_id,label,name,method\nA::000,A,System,Ledger DB,both\n"
                 "A::000,A,Component,WAF,llm\nA::000,A,System,Ledger DB,exact\n")
    assert load_chunk_entities(p) == {"A::000": ["Ledger DB", "WAF"]}
    assert load_chunk_entities(tmp_path / "missing.csv") == {}
