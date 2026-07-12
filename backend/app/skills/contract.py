import hashlib
import io
import re
from typing import Literal

from pydantic import BaseModel, Field
from pypdf import PdfReader
import pypdfium2 as pdfium

from app.llm import LLMError, OpenAICompatibleLLM


RiskLevel = Literal["明确违反强制性规则", "疑似无效或可能不成为合同内容", "对承租人明显不利", "信息缺失"]


class LegalSource(BaseModel):
    title: str
    provision: str
    url: str
    effective_from: str
    jurisdiction: str = "全国"
    checked_at: str = "2026-07-13"


class ContractFinding(BaseModel):
    rule_id: str
    risk_level: RiskLevel
    clause_excerpt: str
    explanation: str
    suggestion: str
    sources: list[LegalSource]


class ContractReviewReport(BaseModel):
    document_hash: str
    filename: str
    city: str
    overall_risk: str
    findings: list[ContractFinding]
    llm_enhanced: bool = False
    llm_tokens: int = 0
    ocr_used: bool = False
    extraction_warnings: list[str] = Field(default_factory=list)
    disclaimer: str = "本结果是基于现行公开法律规则的辅助核验，不替代律师意见或司法机关对具体案件的认定。"


SHANGHAI条例 = LegalSource(title="上海市住房租赁条例", provision="第十五条、第十六条", url="https://fgj.sh.gov.cn/gzdt/20221202/f72b607956b34fc4878d55b5a6a9d064.html", effective_from="2023-02-01", jurisdiction="上海市")
民法典维修 = LegalSource(title="中华人民共和国民法典", provision="租赁合同及格式条款相关规定", url="https://gdca.miit.gov.cn/zwgk/zcwj/flfg/art/2020/art_573d6ef5018b46b6a4e1f31ca085a710.html", effective_from="2021-01-01")
房屋租赁解释 = LegalSource(title="最高人民法院关于审理城镇房屋租赁合同纠纷案件具体应用法律若干问题的解释", provision="第二条、第三条", url="https://gongbao.court.gov.cn/Details/1ba2a85c913753569685966e8ee1e6.html", effective_from="2009-09-01")
国家条例 = LegalSource(title="住房租赁条例", provision="现行行政法规", url="https://xzfg.moj.gov.cn/mobile/law/detail?LawID=1774&Query=", effective_from="2025-09-15")


class RentalContractReviewSkill:
    name = "rental_contract_review"

    def __init__(self, llm: OpenAICompatibleLLM | None = None):
        self.llm = llm

    def extract_text(self, filename: str, content: bytes) -> str:
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("合同文件不能超过 10MB")
        suffix = filename.lower().rsplit(".", 1)[-1]
        if suffix == "pdf":
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > 80:
                raise ValueError("合同 PDF 不能超过 80 页")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix in {"txt", "md"}:
            text = content.decode("utf-8-sig")
        else:
            raise ValueError("当前仅支持 PDF、TXT 和 Markdown 合同")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 30:
            raise ValueError("未能提取到足够的合同文本；扫描 PDF 需要先进行 OCR")
        return text[:120_000]

    @staticmethod
    def pdf_images(content: bytes) -> list[tuple[bytes, str]]:
        document = pdfium.PdfDocument(content)
        if len(document) > 80:
            raise ValueError("合同 PDF 不能超过 80 页")
        images: list[tuple[bytes, str]] = []
        for page in document:
            bitmap = page.render(scale=1.6)
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="JPEG", quality=85)
            images.append((buffer.getvalue(), "image/jpeg"))
        return images

    @staticmethod
    def _excerpt(text: str, match: re.Match, radius: int = 70) -> str:
        return text[max(0, match.start() - radius): min(len(text), match.end() + radius)].replace("\n", " ")

    def apply_rules(self, text: str) -> list[ContractFinding]:
        findings: list[ContractFinding] = []
        rules = [
            ("non_residential_space", r"(厨房|卫生间|阳台|储藏室|贮藏室).{0,12}(出租|居住|住人)", "明确违反强制性规则", "非居住空间被约定单独出租用于居住，触发上海住房出租条件风险。", "核实实际出租空间，删除将非居住空间作为独立居住单元的约定。", [SHANGHAI条例, 国家条例]),
            ("illegal_building", r"(未取得|没有).{0,12}(建设工程规划许可证|规划许可)|违法建筑|违章建筑", "疑似无效或可能不成为合同内容", "文本显示房屋可能缺少规划许可；符合司法解释条件时，租赁合同可能被认定无效。", "签约前核验不动产权属、规划许可及主管部门批准文件。", [房屋租赁解释]),
            ("all_repairs_tenant", r"(任何|全部|所有).{0,8}维修.{0,12}(承租人|乙方).{0,6}(承担|负责)|维修.{0,10}(全部|一律).{0,8}(承租人|乙方)", "对承租人明显不利", "条款可能不区分自然损耗、出租人维修义务与承租人过错，扩大了承租人责任。", "明确房屋主体和自然损耗由出租人维修，承租人仅承担因自身过错造成的损坏。", [民法典维修]),
            ("unannounced_entry", r"(出租人|甲方).{0,12}(随时|无需通知).{0,8}(进入|检查).{0,8}(房屋|房间)", "对承租人明显不利", "条款允许出租方无通知进入承租空间，可能严重影响承租人的正常占有使用。", "约定除紧急情况外应提前合理通知并取得承租人配合。", [国家条例]),
            ("deposit_forfeit", r"押金.{0,12}(概不退还|一律不退|不予退还)", "疑似无效或可能不成为合同内容", "押金无条件不退可能构成明显加重承租人责任的格式条款，仍需结合提示说明义务和违约事实判断。", "把押金扣除限定为有证据的欠费、损坏或约定违约，并明确结算与返还期限。", [国家条例]),
            ("excessive_penalty", r"违约金.{0,10}(三倍|四倍|五倍|[3-9]个月|三个月|四个月|五个月).{0,6}(租金|房租)", "对承租人明显不利", "约定的违约金可能明显高于可预见损失，存在请求调整的风险。", "将违约金与实际损失、剩余租期和重新出租成本合理关联。", [民法典维修]),
            ("ban_early_termination", r"(任何情况|无论何种原因).{0,10}(不得|不允许).{0,6}(提前解除|退租)", "对承租人明显不利", "绝对排除提前解除可能忽略法定解除事由并显著限制承租人权利。", "保留法定解除权，并明确一般提前退租的通知期限与合理责任。", [民法典维修]),
            ("unlimited_fee_change", r"(出租人|甲方).{0,12}(有权|可以).{0,8}(随时|单方).{0,8}(提高|调整).{0,6}(租金|费用)", "疑似无效或可能不成为合同内容", "出租方可单方任意调价的约定缺少明确标准，可能构成不合理格式条款。", "约定固定租金或客观、明确且双方可核验的调价机制。", [民法典维修, 国家条例]),
            ("forced_sublease_liability", r"转租.{0,10}(一切|全部).{0,8}(责任|费用).{0,8}(承租人|乙方)", "对承租人明显不利", "转租责任约定过于笼统，可能把非承租人原因造成的责任一并转嫁。", "明确是否允许转租、同意程序及责任边界。", [民法典维修]),
        ]
        for rule_id, pattern, level, explanation, suggestion, sources in rules:
            for match in list(re.finditer(pattern, text, re.S))[:3]:
                findings.append(ContractFinding(rule_id=rule_id, risk_level=level, clause_excerpt=self._excerpt(text, match), explanation=explanation, suggestion=suggestion, sources=sources))

        required = {
            "主体身份与联系方式": ["出租人", "承租人", "甲方", "乙方", "联系方式"],
            "房屋及设施基本情况": ["房屋地址", "坐落", "附属设施", "设备清单"],
            "租赁期限与交付": ["租赁期限", "租期", "交付日期"],
            "租金与押金": ["租金", "押金"],
            "水电物业等费用": ["水费", "电费", "物业费", "燃气费"],
            "维修责任": ["维修", "修缮"],
            "违约与争议解决": ["违约责任", "争议解决", "人民法院", "仲裁"],
        }
        missing = [name for name, keywords in required.items() if not any(keyword in text for keyword in keywords)]
        if missing:
            findings.append(ContractFinding(rule_id="missing_essential_terms", risk_level="信息缺失", clause_excerpt="未检索到以下关键约定：" + "、".join(missing), explanation="合同可能缺少上海市住房租赁条例列举的一般合同事项。仅凭关键词未检出不能证明合同必然缺失，仍需人工核对版式和附件。", suggestion="在签约前补充并明确缺失事项，尤其是费用、维修、违约和争议解决。", sources=[SHANGHAI条例]))
        return findings

    async def _enhance(self, findings: list[ContractFinding]) -> tuple[bool, int]:
        if not findings or not self.llm or not self.llm.enabled:
            return False, 0
        payload = {"findings": [{"rule_id": item.rule_id, "risk_level": item.risk_level, "clause_excerpt": item.clause_excerpt, "rule_explanation": item.explanation, "rule_suggestion": item.suggestion, "sources": [source.model_dump() for source in item.sources]} for item in findings], "output_schema": {"items": [{"rule_id": "string", "explanation": "string", "suggestion": "string"}]}}
        try:
            result, tokens = await self.llm.complete_json("你是住房租赁合同风险解释器。只能依据输入的条款、规则等级和法律来源进行通俗解释；不得改变风险等级、添加法律条文或宣布最终违法无效。仅输出 JSON。", payload, max_tokens=1600)
            by_rule = {item.get("rule_id"): item for item in result.get("items", [])}
            for finding in findings:
                enhanced = by_rule.get(finding.rule_id)
                if enhanced and isinstance(enhanced.get("explanation"), str): finding.explanation = enhanced["explanation"][:800]
                if enhanced and isinstance(enhanced.get("suggestion"), str): finding.suggestion = enhanced["suggestion"][:500]
            return True, tokens
        except LLMError:
            return False, 0

    async def review(self, filename: str, content: bytes, city: str = "上海") -> ContractReviewReport:
        return await self.review_files([(filename, content, "application/octet-stream")], city)

    async def review_files(self, files: list[tuple[str, bytes, str]], city: str = "上海") -> ContractReviewReport:
        if not files or len(files) > 12:
            raise ValueError("请上传 1 至 12 个合同文件或照片")
        if sum(len(content) for _, content, _ in files) > 30 * 1024 * 1024:
            raise ValueError("合同文件总大小不能超过 30MB")
        text_parts: list[str] = []
        images: list[tuple[bytes, str]] = []
        warnings: list[str] = []
        ocr_tokens = 0
        for filename, content, mime_type in files:
            suffix = filename.lower().rsplit(".", 1)[-1]
            if suffix in {"jpg", "jpeg", "png", "webp"} or mime_type in {"image/jpeg", "image/png", "image/webp"}:
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError(f"单张合同照片不能超过 8MB：{filename}")
                normalized_mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}" if suffix in {"png", "webp"} else mime_type
                images.append((content, normalized_mime))
            elif suffix == "pdf":
                try:
                    text_parts.append(self.extract_text(filename, content))
                except ValueError as exc:
                    if "扫描 PDF" not in str(exc):
                        raise
                    images.extend(self.pdf_images(content))
                    warnings.append(f"{filename} 为扫描型 PDF，已逐页进行 OCR。")
            else:
                text_parts.append(self.extract_text(filename, content))
        if images:
            if not self.llm:
                raise ValueError("合同照片需要配置视觉 OCR 模型")
            try:
                ocr_pages = []
                for start in range(0, len(images), 2):
                    batch_text, batch_tokens = await self.llm.extract_images_text(images[start:start + 2])
                    ocr_pages.append(batch_text)
                    ocr_tokens += batch_tokens
                ocr_text = "\n\n".join(ocr_pages)
                text_parts.append(ocr_text)
                if "[无法识别" in ocr_text:
                    warnings.append("部分照片内容无法识别，请对照原件人工核验。")
            except LLMError as exc:
                raise ValueError("合同照片文字识别失败，请检查清晰度或稍后重试。") from exc
        text = "\n\n".join(text_parts).strip()
        if len(text) < 30:
            raise ValueError("未能提取到足够的合同文本")
        findings = self.apply_rules(text)
        enhanced, tokens = await self._enhance(findings)
        severity = {"明确违反强制性规则": 4, "疑似无效或可能不成为合同内容": 3, "对承租人明显不利": 2, "信息缺失": 1}
        overall = max(findings, key=lambda item: severity[item.risk_level]).risk_level if findings else "未发现预设规则风险"
        digest = hashlib.sha256()
        for filename, content, _ in files:
            digest.update(filename.encode())
            digest.update(content)
        return ContractReviewReport(document_hash=digest.hexdigest(), filename="、".join(filename for filename, _, _ in files), city=city, overall_risk=overall, findings=findings, llm_enhanced=enhanced, llm_tokens=tokens + ocr_tokens, ocr_used=bool(images), extraction_warnings=warnings)
