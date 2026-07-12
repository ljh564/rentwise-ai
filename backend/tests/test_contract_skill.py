import pytest

from app.skills.contract import RentalContractReviewSkill


@pytest.mark.asyncio
async def test_contract_skill_detects_repair_and_missing_terms():
    text = """住房租赁合同\n甲方出租人张某，乙方承租人李某。房屋地址上海市某路1号。租期一年，月租金5000元，押金5000元。任何维修费用全部由承租人承担。"""
    report = await RentalContractReviewSkill().review("contract.txt", text.encode())
    rule_ids = {finding.rule_id for finding in report.findings}
    assert "all_repairs_tenant" in rule_ids
    assert "missing_essential_terms" in rule_ids
    assert report.document_hash


@pytest.mark.asyncio
async def test_contract_skill_rejects_unsupported_file():
    with pytest.raises(ValueError, match="仅支持"):
        await RentalContractReviewSkill().review("contract.docx", b"x" * 100)


class FakeVisionLLM:
    enabled = True

    async def extract_images_text(self, images, max_tokens=5000):
        return "住房租赁合同。甲方出租人，乙方承租人。房屋地址上海市某路1号。所有维修费用全部由承租人承担。租期一年，租金5000元，押金5000元。", 120

    async def complete_json(self, system, payload, max_tokens=1600):
        return {"items": []}, 20


@pytest.mark.asyncio
async def test_contract_photo_uses_ocr_then_rules():
    report = await RentalContractReviewSkill(FakeVisionLLM()).review_files([("page-1.jpg", b"fake-image-content" * 10, "image/jpeg")])
    assert report.ocr_used is True
    assert report.llm_tokens == 140
    assert "all_repairs_tenant" in {finding.rule_id for finding in report.findings}


@pytest.mark.asyncio
async def test_contract_rules_include_versioned_sources_and_penalty():
    text = "住房租赁合同。甲方出租人，乙方承租人，房屋地址上海市某路。租期一年，租金5000元，押金5000元。提前退租违约金为三个月租金。维修责任、物业费、争议解决均另行约定。"
    report = await RentalContractReviewSkill().review("contract.txt", text.encode())
    finding = next(item for item in report.findings if item.rule_id == "excessive_penalty")
    assert finding.sources[0].effective_from
    assert finding.sources[0].checked_at == "2026-07-13"
