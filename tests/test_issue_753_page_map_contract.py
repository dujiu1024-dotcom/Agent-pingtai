from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_MAP = ROOT / "docs" / "frontend" / "page-map.md"
ROUTE_CONTRACT = ROOT / "docs" / "frontend" / "route-contract.md"


def markdown_table(
    document: str,
    section: str,
    header: tuple[str, ...],
) -> list[list[str]]:
    lines = document.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == section),
        None,
    )
    if start is None:
        raise AssertionError(f"missing section: {section}")

    found_header = False
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("### "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not found_header:
            if tuple(cells) == header:
                found_header = True
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if len(cells) != len(header):
            raise AssertionError(
                f"table row in {section} has {len(cells)} cells; expected {len(header)}"
            )
        rows.append(cells)

    if not found_header:
        raise AssertionError(f"missing table header in {section}: {header}")
    return rows


def route_shape_is_valid(path: str) -> bool:
    if not path.startswith("/") or path == "/" or "?" in path or "#" in path:
        return False
    for segment in path.strip("/").split("/"):
        if segment.startswith(":"):
            if not re.fullmatch(r":[A-Za-z][A-Za-z0-9]*", segment):
                return False
        elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", segment):
            return False
    return True


class PageMapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page_map = PAGE_MAP.read_text(encoding="utf-8")
        cls.route_contract = ROUTE_CONTRACT.read_text(encoding="utf-8")
        cls.pages = markdown_table(
            cls.page_map,
            "## 2. 页面清单",
            ("page_id", "页面", "路由", "类型 / 主实体", "主责", "权限关闭", "权限启用"),
        )
        cls.details = markdown_table(
            cls.page_map,
            "## 3. 实体详情唯一归属与穿透",
            ("实体", "唯一详情路由", "主责", "常见入口"),
        )
        cls.api_rows = markdown_table(
            cls.page_map,
            "### 11.4 API 与字段映射索引",
            ("API 索引 ID", "当前核对范围（相对路径）", "当前状态", "关联页面 / 主责"),
        )
        cls.metadata = markdown_table(
            cls.page_map,
            "### 11.7 page_id 逐页元数据索引（主键、面包屑、入口/出口、API）",
            ("page_id", "主实体 / 主键", "面包屑", "主要入口 → 出口", "API 映射索引"),
        )

    def test_page_ids_and_canonical_paths_are_unique_and_well_formed(self):
        page_ids = [row[0] for row in self.pages]
        paths = [row[2] for row in self.pages]
        self.assertEqual(len(page_ids), len(set(page_ids)))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(len(self.pages), 39)
        for row in self.pages:
            self.assertRegex(row[0], r"^[A-Z][A-Z0-9-]+$")
            self.assertTrue(route_shape_is_valid(row[2]), row[2])
            self.assertEqual(len(re.findall(r"#\d+", row[4])), 1, row[0])
            self.assertTrue(row[5] and row[6], row[0])

    def test_entity_detail_ownership_is_unique(self):
        entities = [row[0] for row in self.details]
        paths = [row[1] for row in self.details]
        self.assertEqual(len(entities), len(set(entities)))
        self.assertEqual(len(paths), len(set(paths)))
        for row in self.details:
            self.assertTrue(route_shape_is_valid(row[1]), row[1])
            self.assertEqual(len(re.findall(r"#\d+", row[2])), 1, row[0])

    def test_every_page_has_one_metadata_row_and_valid_api_references(self):
        page_ids = {row[0] for row in self.pages}
        metadata_ids = [row[0] for row in self.metadata]
        self.assertEqual(set(metadata_ids), page_ids)
        self.assertEqual(len(metadata_ids), len(set(metadata_ids)))

        api_ids = {row[0] for row in self.api_rows}
        for row in self.metadata:
            refs = re.findall(r"API-[A-Z0-9-]+", row[4])
            self.assertTrue(refs, row[0])
            self.assertTrue(set(refs).issubset(api_ids), (row[0], refs))

    def test_api_mapping_states_use_documented_vocabulary(self):
        allowed = re.compile(
            r"\b(source-registered|conditional|module-only-unregistered|missing|proposed|partial)\b"
        )
        api_ids = [row[0] for row in self.api_rows]
        self.assertEqual(len(api_ids), len(set(api_ids)))
        for row in self.api_rows:
            self.assertRegex(row[0], r"^API-[A-Z0-9-]+$")
            self.assertRegex(row[2], allowed)

    def test_both_legacy_crosswalks_map_to_canonical_pages(self):
        canonical = {row[0]: row[2] for row in self.pages}
        crosswalks = (
            (
                "### 11.1 `/app` 静态工作台 view key 全量清单",
                ("当前 view key", "当前显示名", "目标 page_id", "目标 path", "当前状态 / 迁移说明"),
                13,
            ),
            (
                "### 11.2 旧版 `platform-ui/index.html` 页面全量清单",
                (
                    "旧版 view key / section",
                    "页面",
                    "目标 page_id",
                    "目标 path",
                    "当前状态 / 迁移说明",
                ),
                13,
            ),
        )
        for section, header, expected_count in crosswalks:
            rows = markdown_table(self.page_map, section, header)
            self.assertEqual(len(rows), expected_count, section)
            for row in rows:
                page_id = row[2]
                path = row[3].split("?", 1)[0]
                self.assertIn(page_id, canonical, (section, page_id))
                self.assertEqual(path, canonical[page_id], (section, page_id, path))

    def test_route_contract_preserves_safe_deep_link_and_dual_mode_rules(self):
        required = (
            "auth.enabled=false",
            "auth.enabled=true",
            "same-origin relative path",
            "双斜杠",
            "反斜杠",
            "CRLF",
            "多次编码绕过",
            "不生成匿名用户",
            "服务端 API 始终再次授权",
        )
        for phrase in required:
            self.assertIn(phrase, self.route_contract)

    def test_mermaid_fences_are_balanced_and_table_remains_authoritative(self):
        fences = re.findall(r"^\`\`\`(?:mermaid)?\s*$", self.page_map, re.MULTILINE)
        self.assertEqual(len(fences) % 2, 0)
        self.assertIn("## 2. 页面清单", self.page_map)
        self.assertIn("## 3. 实体详情唯一归属与穿透", self.page_map)


if __name__ == "__main__":
    unittest.main()
