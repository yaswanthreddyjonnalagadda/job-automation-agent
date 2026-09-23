"""
core/parser.py - Deep Shadow DOM traversal and Viewport Page Signature generator.
Extracts all interactive input elements across web components and generates MD5 state hashes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, List, Optional
from playwright.async_api import Page

SHADOW_DOM_RECURSIVE_TRAVERSAL_SCRIPT = """
(() => {
    function getAllInteractiveElements(root) {
        const elements = [];

        function traverse(node) {
            if (!node) return;

            // Check if this node is an interactive control
            if (node.nodeType === Node.ELEMENT_NODE) {
                const tag = node.tagName.toLowerCase();
                const role = node.getAttribute('role') || '';
                const type = (node.getAttribute('type') || '').toLowerCase();

                const isInput = tag === 'input' && !['hidden', 'submit', 'button', 'reset', 'image'].includes(type);
                const isSelect = tag === 'select';
                const isTextarea = tag === 'textarea';
                const isCustomInteractive = ['combobox', 'checkbox', 'radio', 'textbox', 'listbox'].includes(role);
                const isUpload = tag === 'input' && type === 'file';
                const isButton = tag === 'button' || role === 'button' || (tag === 'input' && ['submit', 'button'].includes(type));

                if (isInput || isSelect || isTextarea || isCustomInteractive || isUpload || isButton) {
                    // Extract label text
                    let label = '';
                    if (node.id) {
                        const lblElem = document.querySelector(`label[for="${node.id}"]`);
                        if (lblElem) label = lblElem.innerText || '';
                    }
                    if (!label && node.closest('label')) {
                        label = node.closest('label').innerText || '';
                    }
                    if (!label) {
                        label = node.getAttribute('aria-label') || node.getAttribute('placeholder') || '';
                    }
                    if (!label && node.parentElement) {
                        // Check preceding sibling or parent text
                        const prev = node.previousElementSibling;
                        if (prev && ['label', 'span', 'p'].includes(prev.tagName.toLowerCase())) {
                            label = prev.innerText || '';
                        }
                    }

                    // For select elements, collect all choices
                    let options = [];
                    if (isSelect) {
                        options = Array.from(node.querySelectorAll('option')).map(opt => ({
                            value: opt.value || opt.innerText,
                            text: opt.innerText.trim()
                        }));
                    }

                    elements.push({
                        tag: tag,
                        role: role,
                        type: type,
                        id: node.id || '',
                        name: node.getAttribute('name') || '',
                        label: label.trim(),
                        placeholder: node.getAttribute('placeholder') || '',
                        required: node.required || node.getAttribute('aria-required') === 'true' || label.includes('*'),
                        value: node.value || node.getAttribute('aria-valuenow') || '',
                        checked: node.checked || node.getAttribute('aria-checked') === 'true',
                        options: options,
                        disabled: node.disabled || node.getAttribute('aria-disabled') === 'true',
                        selector: node.id ? `#${CSS.escape(node.id)}` : ''
                    });
                }
            }

            // If node has an open Shadow Root, descend into it
            if (node.shadowRoot) {
                for (const child of node.shadowRoot.childNodes) {
                    traverse(child);
                }
            }

            // Descend into standard child nodes
            for (const child of node.childNodes) {
                traverse(child);
            }
        }

        traverse(root);
        return elements;
    }

    // Extract visible headings and breadcrumbs for state signature
    function getVisibleHeadings() {
        const headings = [];
        const candidates = document.querySelectorAll('h1, h2, h3, h4, [role="heading"], [class*="step"], [class*="progress"]');
        for (const el of candidates) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && el.innerText) {
                headings.push(el.innerText.trim());
            }
        }
        return headings;
    }

    return {
        controls: getAllInteractiveElements(document.body),
        headings: getVisibleHeadings()
    };
})();
"""


@dataclass
class FormElement:
    tag: str
    role: str
    type: str
    id: str
    name: str
    label: str
    placeholder: str
    required: bool
    value: str
    checked: bool
    options: list[dict[str, str]] = field(default_factory=list)
    disabled: bool = False
    selector: str = ""

    @property
    def identifier(self) -> str:
        """Best stable identifier for matching."""
        return self.id or self.name or self.label or self.selector


@dataclass
class DOMSnapshot:
    signature: str
    url: str
    headings: list[str]
    elements: list[FormElement]
    raw_controls: list[dict[str, Any]]


class DOMParser:
    """Parses page DOM traversing open Shadow DOM boundaries and computes signature."""

    @staticmethod
    async def extract_snapshot(page: Page) -> DOMSnapshot:
        """Scrapes interactive controls across open Shadow Roots and builds state signature."""
        data = await page.evaluate(SHADOW_DOM_RECURSIVE_TRAVERSAL_SCRIPT)
        headings = data.get("headings", [])
        raw_controls = data.get("controls", [])

        elements: list[FormElement] = []
        for rc in raw_controls:
            elements.append(
                FormElement(
                    tag=rc.get("tag", ""),
                    role=rc.get("role", ""),
                    type=rc.get("type", ""),
                    id=rc.get("id", ""),
                    name=rc.get("name", ""),
                    label=rc.get("label", ""),
                    placeholder=rc.get("placeholder", ""),
                    required=bool(rc.get("required", False)),
                    value=rc.get("value", ""),
                    checked=bool(rc.get("checked", False)),
                    options=rc.get("options", []),
                    disabled=bool(rc.get("disabled", False)),
                    selector=rc.get("selector", ""),
                )
            )

        # Generate Viewport Page Signature
        # Combines visible headings and sorted control labels/ids
        control_tokens = sorted([f"{el.tag}:{el.identifier}" for el in elements if not el.disabled])
        signature_raw = f"{'|'.join(headings)}||{'|'.join(control_tokens)}"
        signature_md5 = hashlib.md5(signature_raw.encode("utf-8")).hexdigest()

        return DOMSnapshot(
            signature=signature_md5,
            url=page.url,
            headings=headings,
            elements=elements,
            raw_controls=raw_controls,
        )

