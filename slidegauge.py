#!/usr/bin/env python3
"""
SlideGauge - Static analyzer for Marp Markdown decks

ABOUTME: A single-file, zero-dependency static analyzer for Marp Markdown
ABOUTME: Uses fence-aware parser, rule registry, and stdio JSON protocol
ABOUTME: Produces deterministic scores, buckets, and SARIF for AI agents
"""

import sys
import json
import hashlib
import uuid
import argparse
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional, FrozenSet
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULTS = {
  "threshold": 70,
  "pass_rate": 0.8,
  "rules": {
    "title": { "required": True, "max_main": 35, "max_sub": 50 },
    "content": {
      "max_chars": 350, "min_chars": 50, "exercise_max_chars": 450,
      "max_lines": 15, "max_bullets": 6, "max_bullet_len": 80
    },
    "code": { "max_simple": 10, "max_complex": 5, "max_line_len": 100 },
    "special": { "table_char_eq": 100, "chart_char_eq": 100, "code_char_eq": 150 },
    "color": { "min_contrast_warn": 4.5, "min_contrast_error": 3.0, "max_colors": 6 }
  },
  "weights": {
    "title/required": 20,
    "title/too_long": 10,
    "content/too_long": 15,
    "content/too_short": 5,
    "bullets/too_many": 10,
    "bullets/too_long": 5,
    "code/too_long": 8,
    "code/long_line": 5,
    "lines/too_many": 10,
    "accessibility/alt_required": 8,
    "links/bare_urls": 3,
    "structure/duplicate_titles": 5,
    "meta/theme_required": 5,
    "color/low_contrast": 10,
    "color/too_many": 5,
    "color/unlabeled": 5
  },
  "buckets": {
    "content": ["title/*","content/*","bullets/*","lines/*","structure/*"],
    "code":    ["code/*"],
    "layout":  ["meta/*","structure/*"],
    "a11y":    ["accessibility/*","links/*"],
    "color":   ["color/*"]
  }
}

# ============================================================================
# UTILITIES
# ============================================================================

def deep_merge(a: dict, b: dict) -> dict:
    """Deep merge two dictionaries"""
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            deep_merge(a[k], v)
        else:
            a[k] = v
    return a

def sha1(data: str) -> str:
    """Generate SHA1 hash"""
    return "sha1:" + hashlib.sha1(data.encode('utf-8')).hexdigest()

def uuid5_of(data: str) -> str:
    """Generate UUID5 of data"""
    NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")
    return "uuid5:" + str(uuid.uuid5(NAMESPACE, data))

def json_dumps_canonical(obj: Any) -> str:
    """Canonical JSON representation"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',',':'))

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str       # "error" | "warning" | "info"
    message: str
    deduction: int
    patch: Tuple = field(default_factory=tuple)  # tuple of dicts for determinism

@dataclass(frozen=True)
class Slide:
    index: int
    uuid: str           # uuid5 of normalized body
    title: str
    body: str
    metrics: dict       # deterministic primitive-only (ints, floats, bools, tuples)
    overrides: dict     # {"disabled":[...], "rules":{...}}  per-slide

@dataclass(frozen=True)
class SlideResult:
    index: int
    uuid: str
    title: str
    body: str
    metrics: dict
    diagnostics: Tuple[Finding, ...]
    score: int
    bucket_scores: dict  # optional per-slide

# ============================================================================
# PARSER
# ============================================================================

def split_slides(lines: List[str]) -> List[str]:
    """Fence-aware slide splitter using state machine"""
    slides, buf, state, fence = [], [], "BODY", None

    def is_fence(l: str) -> Optional[str]:
        s = l.lstrip()
        if s.startswith("```"): return "```"
        if s.startswith("~~~"): return "~~~"
        return None

    # Handle initial frontmatter specially
    i = 0
    if i < len(lines) and lines[i].strip() == '---':
        i += 1
        # Skip frontmatter content
        while i < len(lines) and lines[i].strip() != '---':
            i += 1
        i += 1  # Skip closing fence
        # Continue from the line after closing fence

    for ln in lines[i:]:
        if state == "BODY":
            f = is_fence(ln)
            if f:
                state, fence = "CODE", f
                buf.append(ln)
                continue
            if ln.strip() == "---":
                if buf:  # Only add slide if there's content
                    slides.append(buf)
                buf = []
                continue
            buf.append(ln)
        else:  # CODE
            buf.append(ln)
            if ln.lstrip().startswith(fence):
                state, fence = "BODY", None

    if buf:
        slides.append(buf)

    return ["\n".join(s).strip() for s in slides if "\n".join(s).strip()]

def extract_frontmatter(slide_text: str) -> Tuple[dict, str]:
    """Extract frontmatter and return meta + remaining text"""
    lines = slide_text.split('\n')
    meta = {}
    content_lines = []
    i = 0

    # Skip initial fence if present
    if i < len(lines) and lines[i].strip() == '---':
        i += 1
        # Parse YAML frontmatter
        while i < len(lines) and lines[i].strip() != '---':
            line = lines[i].strip()
            if ':' in line:
                key, value = line.split(':', 1)
                meta[key.strip()] = value.strip()
            i += 1
        i += 1  # Skip closing fence

    # Remaining lines are content
    content_lines = lines[i:]

    return meta, '\n'.join(content_lines).strip()

def parse_inline_overrides(lines: List[str]) -> dict:
    """Parse inline rule control comments"""
    disabled = []
    local_cfg = {}

    for ln in lines:
        s = ln.strip()
        if s.startswith("<!--") and "slidegauge:" in s:
            content = s[s.find("slidegauge:")+11:].rstrip("-->").strip()
            if content.startswith("disable"):
                _, rule_id = content.split(None, 1)
                disabled.append(rule_id.strip())
            else:
                try:
                    patch = json.loads(content)
                    deep_merge(local_cfg, patch)
                except Exception:
                    pass  # invalid snippet ignored deterministically

    return {"disabled": sorted(set(disabled)), "rules": local_cfg}

def scan_slide(text: str, cfg: dict) -> dict:
    """Comprehensive feature scan of slide content"""
    lines = text.split('\n')
    metrics = {
        "title_length": 0,
        "content_chars": 0,
        "content_chars_adjusted": 0,
        "bullets": 0,
        "lines": len(lines),
        "code_blocks": tuple(),
        "has_table": False,
        "has_chart": False,
        "is_exercise": False,
        "images": tuple(),
        "colors": tuple(),
        "min_contrast": None,
        "unique_colors": 0,
        "bare_urls": 0
    }

    # Extract title (first # heading, fallback to ## if no # found)
    title_line_index = -1
    title_text = ""

    # First try to find a # heading
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('# '):
            # Extract only the first heading from malformed lines like "# Title## Subtitle### More"
            title_text = stripped.split('##')[0].lstrip('#').strip()
            metrics["title_length"] = len(title_text)
            title_line_index = i
            break

    # If no # heading found, look for ## heading as fallback
    if not title_text:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('## '):
                # Extract the ## heading and handle malformed markdown like "## Title### Subtitle"
                parts = stripped.split('###')[0]  # Split on ### and take first part
                title_text = parts[3:].strip()  # Remove "## " prefix
                metrics["title_length"] = len(title_text)
                title_line_index = i
                break

    # Scan content
    in_code_block = False
    code_fence = None
    code_content = []
    code_start_line = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Code block detection
        if not in_code_block and (stripped.startswith('```') or stripped.startswith('~~~')):
            in_code_block = True
            code_fence = stripped[:3]
            code_content = []
            code_start_line = i
            continue
        elif in_code_block and stripped.startswith(code_fence):
            in_code_block = False
            lang = stripped[3:].strip()
            code_lines = len(code_content)
            metrics["code_blocks"] = tuple(metrics["code_blocks"] + ((code_lines, lang),))
            code_content = []
            continue

        if in_code_block:
            code_content.append(line)
            continue

        # Skip title line entirely from content calculation
        if i == title_line_index:
            continue

        # Content character count
        metrics["content_chars"] += len(line)

        # Adjusted character count (special content gets fixed weighting)
        adjusted_chars = 0
        if '| ' in line and ' | ' in line:  # Table row
            adjusted_chars = cfg["rules"]["special"]["table_char_eq"]
            metrics["has_table"] = True
        elif 'mermaid' in stripped.lower():  # Chart
            adjusted_chars = cfg["rules"]["special"]["chart_char_eq"]
            metrics["has_chart"] = True
        else:
            adjusted_chars = len(line)

        metrics["content_chars_adjusted"] += adjusted_chars

        # Bullet detection
        bullet_match = re.match(r'^\s*[-*+]\s+', line)
        if bullet_match:
            metrics["bullets"] += 1

        # Table detection
        if '|' in line:
            metrics["has_table"] = True

        # Chart detection
        if 'mermaid' in stripped.lower():
            metrics["has_chart"] = True

        # Exercise detection (more specific patterns)
        if re.search(r'\b(exercise|practice)\b', line, re.IGNORECASE):
            metrics["is_exercise"] = True
        # Check for exercise divs/classes
        if 'class="exercise"' in line or '<div class="exercise"' in line:
            metrics["is_exercise"] = True

        # Image detection
        img_match = re.findall(r'!\[(.*?)\]\((.*?)\)', line)
        for alt_text, url in img_match:
            metrics["images"] = tuple(metrics["images"] + ((alt_text, url),))

        # Color detection
        color_matches = re.findall(r'color:\s*([^;\'"]+)', line, re.IGNORECASE)
        for color_val in color_matches:
            rgb = parse_color(color_val)
            if rgb:
                contrast = contrast_ratio(rgb)
                metrics["colors"] = tuple(metrics["colors"] + ((color_val, rgb, contrast),))

        # Bare URL detection (not in markdown link syntax)
        if re.search(r'https?://[^\s\)]+', line):
            # Check if URL is NOT inside markdown link syntax []()
            if not re.search(r'\[([^\]]*)\]\(https?://[^\)]+\)', line):
                metrics["bare_urls"] += 1

    # Calculate color metrics
    if metrics["colors"]:
        contrasts = [c[2] for c in metrics["colors"]]
        metrics["min_contrast"] = min(contrasts)
        unique_colors = len(set(c[1] for c in metrics["colors"]))
        metrics["unique_colors"] = unique_colors

    return metrics

def parse_color(s: str) -> Optional[Tuple[int, int, int]]:
    """Parse color string to RGB tuple"""
    s = s.strip().lower()

    # Hex color
    if s.startswith("#") and len(s) == 7:
        try:
            return (int(s[1:3],16), int(s[3:5],16), int(s[5:7],16))
        except:
            return None

    # RGB color
    if s.startswith("rgb(") and s.endswith(")"):
        try:
            parts = s[4:-1].split(",")
            r,g,b = [int(p.strip()) for p in parts[:3]]
            if 0<=r<=255 and 0<=g<=255 and 0<=b<=255:
                return (r,g,b)
        except:
            return None

    return None

def rel_lum(rgb: Tuple[int, int, int]) -> float:
    """Calculate relative luminance"""
    r,g,b = [x/255.0 for x in rgb]
    return 0.2126*r + 0.7152*g + 0.0722*b

def contrast_ratio(rgb: Tuple[int, int, int], bg: Tuple[int, int, int] = (255,255,255)) -> float:
    """Calculate contrast ratio"""
    L1, L2 = rel_lum(rgb), rel_lum(bg)
    hi, lo = (L1, L2) if L1 > L2 else (L2, L1)
    return (hi + 0.05) / (lo + 0.05)

def parse_slides(markdown: str) -> Tuple[Slide, ...]:
    """Parse markdown into Slide objects"""
    lines = markdown.split('\n')
    slide_texts = split_slides(lines)
    slides = []

    for i, slide_text in enumerate(slide_texts):
        # Extract frontmatter
        meta, content = extract_frontmatter(slide_text)

        # Parse inline overrides
        content_lines = content.split('\n')
        overrides = parse_inline_overrides(content_lines)

        # Extract title (first # heading, fallback to ## if no # found)
        title = ""

        # First try to find a # heading
        for line in content_lines:
            stripped = line.strip()
            if stripped.startswith('# '):
                # Extract only the first heading from malformed lines like "# Title## Subtitle### More"
                parts = stripped.split('##')[0]  # Split on ## and take first part
                title = parts.lstrip('#').strip()
                break

        # If no # heading found, look for ## heading as fallback
        if not title:
            for line in content_lines:
                stripped = line.strip()
                if stripped.startswith('## '):
                    # Extract the ## heading and handle malformed markdown like "## Title### Subtitle"
                    parts = stripped.split('###')[0]  # Split on ### and take first part
                    title = parts[3:].strip()  # Remove "## " prefix
                    break

        # Scan features
        metrics = scan_slide(content, DEFAULTS)

        # Create slide object
        slide = Slide(
            index=i,
            uuid=uuid5_of(content.strip()),
            title=title,
            body=content,
            metrics=metrics,
            overrides=overrides
        )
        slides.append(slide)

    return tuple(slides)

# ============================================================================
# RULE REGISTRY
# ============================================================================

REGISTRY = []

class Rule:
    id = "base"
    severity = "warning"  # default
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        return ()

def register(rule):
    REGISTRY.append(rule)
    return rule

# ============================================================================
# CONTENT RULES
# ============================================================================

@register
class TitleRequired(Rule):
    """Every slide needs a clear title (# or ##) for navigation and structure"""
    id = "title/required"
    severity = "error"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        if slide.metrics["title_length"] == 0 and self.id not in slide.overrides.get("disabled", ()):
            return (Finding(self.id, self.severity, "Slide missing title - add # Title or ## Title",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class TitleTooLong(Rule):
    """Titles should be concise (≤35 chars) for readability on slides"""
    id = "title/too_long"
    severity = "warning"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        max_len = cfg["rules"]["title"]["max_main"]
        if slide.metrics["title_length"] > max_len and self.id not in slide.overrides.get("disabled", ()):
            excess = slide.metrics['title_length'] - max_len
            return (Finding(self.id, self.severity,
                           f"Title length {slide.metrics['title_length']} > max {max_len} (shorten by {excess} chars)",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class ContentTooLong(Rule):
    """Keep slides concise: ≤350 chars normal, ≤450 for exercises"""
    id = "content/too_long"
    severity = "warning"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        rules = cfg["rules"]["content"]
        limit = rules["exercise_max_chars"] if slide.metrics.get("is_exercise") else rules["max_chars"]
        val = slide.metrics["content_chars_adjusted"]

        if val > limit and self.id not in slide.overrides.get("disabled", ()):
            excess = val - limit
            return (Finding(self.id, self.severity,
                           f"Adjusted content {val} > max {limit} (reduce by ~{excess} chars or split into 2 slides)",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class ContentTooShort(Rule):
    """Slides need substance: add context, examples, or visuals (≥50 chars)"""
    id = "content/too_short"
    severity = "info"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        min_len = cfg["rules"]["content"]["min_chars"]
        val = slide.metrics["content_chars"]

        if val > 0 and val < min_len and self.id not in slide.overrides.get("disabled", ()):
            needed = min_len - val
            return (Finding(self.id, self.severity,
                           f"Content {val} < min {min_len} (add ~{needed} chars)",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class BulletsTooMany(Rule):
    """Limit bullets to ≤6 per slide for audience comprehension"""
    id = "bullets/too_many"
    severity = "warning"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        max_bullets = cfg["rules"]["content"]["max_bullets"]
        val = slide.metrics["bullets"]

        if val > max_bullets and self.id not in slide.overrides.get("disabled", ()):
            excess = val - max_bullets
            return (Finding(self.id, self.severity,
                           f"{val} bullets > max {max_bullets} (remove {excess} or split slide)",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class LinesTooMany(Rule):
    """Keep slides scannable: ≤15 lines for visual clarity"""
    id = "lines/too_many"
    severity = "warning"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        max_lines = cfg["rules"]["content"]["max_lines"]
        val = slide.metrics["lines"]

        if val > max_lines and self.id not in slide.overrides.get("disabled", ()):
            excess = val - max_lines
            return (Finding(self.id, self.severity,
                           f"{val} lines > max {max_lines} (condense or split into 2 slides)",
                           deduction=cfg["weights"][self.id]),)
        return ()

# ============================================================================
# COLOR RULES
# ============================================================================

@register
class ColorLowContrast(Rule):
    """Text must have sufficient contrast for accessibility (WCAG AA: ≥4.5:1)"""
    id = "color/low_contrast"
    severity = "error"
    bucket = "color"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        warn_thresh = cfg["rules"]["color"]["min_contrast_warn"]
        err_thresh = cfg["rules"]["color"]["min_contrast_error"]
        mc = slide.metrics.get("min_contrast")

        if mc is None or self.id in slide.overrides.get("disabled", ()):
            return ()

        if mc < err_thresh:
            return (Finding(self.id, "error",
                           f"Contrast {mc:.2f} below minimum {err_thresh:.2f} (use darker/lighter colors)",
                           deduction=cfg["weights"][self.id]),)
        elif mc < warn_thresh:
            return (Finding(self.id, "warning",
                           f"Contrast {mc:.2f} below recommended {warn_thresh:.2f} (increase for better readability)",
                           deduction=cfg["weights"][self.id] // 2),)
        return ()

@register
class ColorTooMany(Rule):
    """Limit color palette to ≤6 colors for visual consistency"""
    id = "color/too_many"
    severity = "warning"
    bucket = "color"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        max_colors = cfg["rules"]["color"]["max_colors"]
        unique_colors = slide.metrics.get("unique_colors", 0)

        if unique_colors > max_colors and self.id not in slide.overrides.get("disabled", ()):
            excess = unique_colors - max_colors
            return (Finding(self.id, self.severity,
                           f"{unique_colors} unique colors > max {max_colors} (reduce by {excess})",
                           deduction=cfg["weights"][self.id]),)
        return ()

# ============================================================================
# ACCESSIBILITY RULES
# ============================================================================

@register
class AltRequired(Rule):
    """All images need alt text for screen readers: ![description](url)"""
    id = "accessibility/alt_required"
    severity = "error"
    bucket = "a11y"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        missing_alt = []
        for alt_text, url in slide.metrics.get("images", []):
            if not alt_text.strip():
                missing_alt.append(url)

        if missing_alt and self.id not in slide.overrides.get("disabled", ()):
            return (Finding(self.id, self.severity,
                           f"{len(missing_alt)} images missing alt text (add descriptions in ![alt text](url))",
                           deduction=cfg["weights"][self.id]),)
        return ()

@register
class BareUrls(Rule):
    """Format URLs as links: [text](url) instead of raw https://..."""
    id = "links/bare_urls"
    severity = "info"
    bucket = "a11y"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        bare_count = slide.metrics.get("bare_urls", 0)

        if bare_count > 0 and self.id not in slide.overrides.get("disabled", ()):
            return (Finding(self.id, self.severity,
                           f"{bare_count} bare URLs (use [link text](url) format)",
                           deduction=cfg["weights"][self.id]),)
        return ()

# ============================================================================
# CODE RULES
# ============================================================================

@register
class CodeTooLong(Rule):
    """Code blocks should be short: ≤10 lines simple, ≤5 complex languages"""
    id = "code/too_long"
    severity = "warning"
    bucket = "code"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        max_simple = cfg["rules"]["code"]["max_simple"]
        max_complex = cfg["rules"]["code"]["max_complex"]

        issues = []
        for lines, lang in slide.metrics.get("code_blocks", []):
            max_allowed = max_complex if lang in ['python', 'javascript', 'java', 'cpp'] else max_simple
            if lines > max_allowed:
                excess = lines - max_allowed
                issues.append(f"{lang} code {lines} lines > max {max_allowed} (trim {excess} lines or split)")

        if issues and self.id not in slide.overrides.get("disabled", ()):
            return (Finding(self.id, self.severity,
                           "; ".join(issues),
                           deduction=cfg["weights"][self.id]),)
        return ()

# ============================================================================
# ENGINE
# ============================================================================

def run_rules_on_slide(slide: Slide, cfg: dict) -> Tuple[Tuple[Finding, ...], int, dict]:
    """Run all rules on a slide and return diagnostics, score, and bucket scores"""

    # Apply per-slide local overrides
    effective = json.loads(json_dumps_canonical(cfg))  # clone deterministically
    if slide.overrides.get("rules"):
        deep_merge(effective, slide.overrides["rules"])

    # Run rules
    diags = []
    for rule_class in REGISTRY:
        rule = rule_class()
        if any(rule.id == d for d in slide.overrides.get("disabled", ())):
            continue
        diags.extend(rule.check(slide, effective))

    # Deterministic sort
    diags = tuple(sorted(diags, key=lambda f: (f.rule, f.message)))

    # Calculate score
    total = 100 - sum(max(0, f.deduction) for f in diags)
    total = max(0, min(100, total))

    # Calculate bucket scores
    bucket_scores = {}
    for name, patterns in effective["buckets"].items():
        deduction = 0
        for finding in diags:
            for pattern in patterns:
                if pattern.endswith("*"):
                    if finding.rule.startswith(pattern[:-1]):
                        deduction += max(0, finding.deduction)
                        break
                elif finding.rule == pattern:
                    deduction += max(0, finding.deduction)
                    break
        bucket_scores[name] = max(0, 100 - deduction)

    return diags, total, bucket_scores

def check_duplicate_titles(slides: Tuple[Slide, ...]) -> Dict[int, List[Finding]]:
    """Post-pass to check for duplicate titles"""
    title_counts = {}
    for slide in slides:
        if slide.title:
            title_counts.setdefault(slide.title, []).append(slide.index)

    duplicate_findings = {}
    for title, indices in title_counts.items():
        if len(indices) > 1:
            for idx in indices:
                duplicate_findings.setdefault(idx, []).append(
                    Finding("structure/duplicate_titles", "warning",
                           f"Duplicate title '{title}' found on {len(indices)} slides",
                           deduction=5)
                )

    return duplicate_findings

def evaluate_all(slides: Tuple[Slide, ...], cfg: dict) -> Tuple[SlideResult, ...]:
    """Evaluate all slides and return results"""

    # Check for duplicate titles first
    duplicate_findings = check_duplicate_titles(slides)

    results = []
    for slide in slides:
        diags, score, bucket_scores = run_rules_on_slide(slide, cfg)

        # Add duplicate title findings if any and recalculate score
        if slide.index in duplicate_findings:
            diags = tuple(sorted(diags + tuple(duplicate_findings[slide.index]),
                               key=lambda f: (f.rule, f.message)))
            
            # Recalculate score with duplicate findings included
            total_deduction = sum(max(0, f.deduction) for f in diags)
            score = max(0, min(100, 100 - total_deduction))
            
            # Recalculate bucket scores
            for name, patterns in cfg["buckets"].items():
                deduction = 0
                for finding in diags:
                    for pattern in patterns:
                        if pattern.endswith("*"):
                            if finding.rule.startswith(pattern[:-1]):
                                deduction += max(0, finding.deduction)
                                break
                        elif finding.rule == pattern:
                            deduction += max(0, finding.deduction)
                            break
                bucket_scores[name] = max(0, 100 - deduction)

        result = SlideResult(
            index=slide.index,
            uuid=slide.uuid,
            title=slide.title,
            body=slide.body,
            metrics=slide.metrics,
            diagnostics=diags,
            score=score,
            bucket_scores=bucket_scores
        )
        results.append(result)

    return tuple(results)

# ============================================================================
# CACHING
# ============================================================================

CACHE_FILE = ".slidegauge.cache.json"

def load_cache(path: str) -> dict:
    """Load analysis cache"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_cache(path: str, data: dict):
    """Save analysis cache"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, separators=(',',':'))

def get_cached_results(slides: Tuple[Slide, ...], cache: dict) -> Dict[str, Optional[SlideResult]]:
    """Get cached results for slides, keyed by UUID"""
    cached = {}
    for slide in slides:
        if slide.uuid in cache:
            cache_data = cache[slide.uuid]
            cached[slide.uuid] = SlideResult(
                index=slide.index,
                uuid=slide.uuid,
                title=slide.title,
                body=slide.body,
                metrics=slide.metrics,
                diagnostics=tuple(Finding(**f) for f in cache_data["diagnostics"]),
                score=cache_data["score"],
                bucket_scores=cache_data["bucket_scores"]
            )
        else:
            cached[slide.uuid] = None
    return cached

# ============================================================================
# REPORTING
# ============================================================================

def to_json(slide_results: Tuple[SlideResult, ...], summary: dict, engine_meta: dict) -> dict:
    """Generate JSON report"""
    return {
        "slides": [
            {
                "uuid": r.uuid,
                "title": r.title,
                "body": r.body,
                "metrics": r.metrics,
                "diagnostics": [vars(f) for f in r.diagnostics],
                "score": r.score,
                "bucket_scores": r.bucket_scores
            }
            for r in slide_results
        ],
        "summary": summary,
        "engine": engine_meta
    }

def to_sarif(slide_results: Tuple[SlideResult, ...]) -> dict:
    """Generate SARIF 2.1.0 report"""
    results = []

    for slide_result in slide_results:
        for finding in slide_result.diagnostics:
            results.append({
                "ruleId": finding.rule,
                "level": {
                    "error": "error",
                    "warning": "warning",
                    "info": "note"
                }.get(finding.severity, "note"),
                "message": {"text": finding.message},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "stdin"},
                        "region": {
                            "startLine": slide_result.index + 1,
                            "startColumn": 1
                        }
                    }
                }]
            })

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "SlideGauge",
                    "version": "0.2.0",
                    "informationUri": "https://github.com/marp-team/slidegauge"
                }
            },
            "results": results
        }]
    }

def to_text(slide_results: Tuple[SlideResult, ...], summary: dict) -> str:
    """Generate concise text report"""
    lines = []

    # Per-slide summary
    for result in slide_results:
        status = "✓" if result.score >= summary.get("threshold", 70) else "❌"
        issues = []
        for finding in result.diagnostics:
            issues.append(f"{finding.rule}({finding.deduction})")

        issues_str = ", ".join(issues) if issues else "no issues"
        lines.append(f"Slide {result.index + 1} ({status} {result.score}) • {issues_str}")

    # Overall summary
    bucket_scores = summary.get("bucket_scores", {})
    bucket_str = " ".join([f"{k}={v}" for k, v in bucket_scores.items()])
    passing = sum(1 for r in slide_results if r.score >= summary.get("threshold", 70))

    lines.append(f"SUMMARY: {bucket_str} • avg={summary.get('avg_score', 0):.1f} • "
                f"passing={passing}/{len(slide_results)} • threshold={summary.get('threshold', 70)}")

    return "\n".join(lines)

# ============================================================================
# STDIO PROTOCOL
# ============================================================================

def handle_stdio():
    """Handle stdio protocol for agent communication"""
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            response = process_request(request)
            print(json.dumps(response, separators=(',',':')))
            sys.stdout.flush()
        except Exception as e:
            error_response = {"ok": False, "error": str(e)}
            print(json.dumps(error_response, separators=(',',':')))
            sys.stdout.flush()

def process_request(request: dict) -> dict:
    """Process a single stdio request"""
    op = request.get("op")

    if op == "analyze":
        return handle_analyze(request)
    elif op == "slides":
        return handle_slides(request)
    elif op == "rules":
        return handle_rules(request)
    elif op == "explain":
        return handle_explain(request)
    else:
        return {"ok": False, "error": f"Unknown operation: {op}"}

def handle_analyze(request: dict) -> dict:
    """Handle analyze operation"""
    try:
        document = request.get("document", "")
        config = request.get("config", {})
        parallel = request.get("parallel", False)

        # Merge config with defaults
        effective_cfg = json.loads(json_dumps_canonical(DEFAULTS))
        deep_merge(effective_cfg, config)

        # Parse and analyze
        slides = parse_slides(document)

        # Check cache
        cache_path = CACHE_FILE
        cache = load_cache(cache_path)
        cached_results = get_cached_results(slides, cache)

        # Analyze uncached slides
        uncached_slides = [s for s in slides if not cached_results[s.uuid]]
        if uncached_slides:
            new_results = evaluate_all(tuple(uncached_slides), effective_cfg)

            # Update cache - create UUID to result mapping
            new_results_map = {r.uuid: r for r in new_results}
            for slide in uncached_slides:
                result = new_results_map.get(slide.uuid)
                if result:
                    cache[slide.uuid] = {
                        "diagnostics": [vars(f) for f in result.diagnostics],
                        "score": result.score,
                        "bucket_scores": result.bucket_scores
                    }
                    cached_results[slide.uuid] = result

            save_cache(cache_path, cache)

        # Combine results in original order
        all_results = []
        for slide in slides:
            if cached_results[slide.uuid]:
                all_results.append(cached_results[slide.uuid])

        slide_results = tuple(all_results)

        # Calculate summary
        scores = [r.score for r in slide_results]
        avg_score = sum(scores) / len(scores) if scores else 0

        # Calculate bucket averages
        bucket_scores = {}
        for bucket in effective_cfg["buckets"]:
            bucket_values = [r.bucket_scores.get(bucket, 100) for r in slide_results]
            bucket_scores[bucket] = sum(bucket_values) / len(bucket_values) if bucket_values else 100

        summary = {
            "total_slides": len(slides),
            "avg_score": round(avg_score, 1),
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "threshold": effective_cfg["threshold"],
            "bucket_scores": bucket_scores,
            "passing": sum(1 for s in scores if s >= effective_cfg["threshold"]),
            "total_issues": sum(len(r.diagnostics) for r in slide_results)
        }

        # Engine metadata
        engine_meta = {
            "version": "0.2.0",
            "config_checksum": sha1(json_dumps_canonical(effective_cfg)),
            "rule_order": sorted([r.id for r in REGISTRY])
        }

        return {
            "ok": True,
            "result": to_json(slide_results, summary, engine_meta)
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}

def handle_slides(request: dict) -> dict:
    """Handle slides operation"""
    try:
        document = request.get("document", "")
        slides = parse_slides(document)

        return {
            "ok": True,
            "slides": [
                {
                    "index": s.index,
                    "uuid": s.uuid,
                    "title": s.title,
                    "line_count": len(s.body.split('\n'))
                }
                for s in slides
            ]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def handle_rules(request: dict) -> dict:
    """Handle rules operation"""
    try:
        return {
            "ok": True,
            "rules": [
                {
                    "id": r().id,
                    "severity": r().severity,
                    "bucket": r().bucket
                }
                for r in REGISTRY
            ]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def handle_explain(request: dict) -> dict:
    """Handle explain operation"""
    try:
        rule_id = request.get("rule")
        rule_map = {r().id: r for r in REGISTRY}

        if rule_id not in rule_map:
            return {"ok": False, "error": f"Unknown rule: {rule_id}"}

        rule_class = rule_map[rule_id]
        rule = rule_class()

        return {
            "ok": True,
            "rule": {
                "id": rule.id,
                "severity": rule.severity,
                "bucket": rule.bucket,
                "description": rule.__doc__ or f"Rule {rule.id}"
            }
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ============================================================================
# SELF-TEST
# ============================================================================

def selftest():
    """Run self-test suite"""
    tests_passed = 0
    tests_failed = 0

    def assert_true(condition, msg):
        nonlocal tests_passed, tests_failed
        if condition:
            tests_passed += 1
        else:
            tests_failed += 1
            print(f"FAIL: {msg}")

    # Test 1: Fence-aware splitting
    markdown1 = "# Slide 1\n```\ncode with ---\n```\n---\n# Slide 2"
    slides1 = parse_slides(markdown1)
    assert_true(len(slides1) == 2, f"Expected 2 slides, got {len(slides1)}")
    assert_true("code with ---" in slides1[0].body, "Code block should contain ---")

    # Test 2: Color contrast detection
    markdown2 = '# Slide\n<span style="color: #aaaaaa">Light text</span>'
    slides2 = parse_slides(markdown2)
    assert_true(len(slides2) == 1, "Should parse single slide")
    assert_true(slides2[0].metrics.get("unique_colors", 0) > 0, "Should detect colors")

    # Test 3: Title extraction
    markdown3 = "# Main Title\nContent"
    slides3 = parse_slides(markdown3)
    assert_true(slides3[0].title == "Main Title", f"Expected 'Main Title', got '{slides3[0].title}'")

    # Test 4: Duplicate titles
    markdown4 = "# Same\nContent 1\n---\n# Same\nContent 2\n---\n# Same\nContent 3"
    slides4 = parse_slides(markdown4)
    cfg = json.loads(json_dumps_canonical(DEFAULTS))
    results4 = evaluate_all(slides4, cfg)
    duplicate_count = sum(1 for r in results4 if any(f.rule == "structure/duplicate_titles" for f in r.diagnostics))
    assert_true(duplicate_count == 3, f"Expected 3 duplicate title findings, got {duplicate_count}")

    # Test 5: Image alt text detection
    markdown5 = '# Slide\n![Alt text](image.png)\n![](no-alt.png)'
    slides5 = parse_slides(markdown5)
    assert_true(len(slides5[0].metrics.get("images", [])) == 2, "Should detect 2 images")

    # Test 6: JSON schema validation
    slides6 = parse_slides("# Test\nContent")
    results6 = evaluate_all(slides6, cfg)
    json_report = to_json(results6, {"threshold": 70}, {"version": "0.2.0", "config_checksum": "test", "rule_order": []})
    assert_true("slides" in json_report, "JSON report should have slides key")
    assert_true("summary" in json_report, "JSON report should have summary key")
    assert_true("engine" in json_report, "JSON report should have engine key")

    # Test 7: Rule registration
    assert_true(len(REGISTRY) > 0, "Should have registered rules")
    rule_ids = [r.id for r in REGISTRY]
    assert_true("title/required" in rule_ids, "Should have title/required rule")
    assert_true("color/low_contrast" in rule_ids, "Should have color/low_contrast rule")

    if tests_failed == 0:
        print("SELFTEST: OK")
        return 0
    else:
        print(f"SELFTEST: FAILED ({tests_failed}/{tests_passed + tests_failed})")
        return 1

# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="SlideGauge - Static analyzer for Marp Markdown decks"
    )
    parser.add_argument("input", nargs="?", help="Input markdown file (default: stdin)")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("-c", "--config", help="Configuration JSON file")
    parser.add_argument("--json", action="store_true", help="JSON output format")
    parser.add_argument("--text", action="store_true", help="Text output format")
    parser.add_argument("--sarif", action="store_true", help="SARIF output format")
    parser.add_argument("--threshold", type=int, help="Score threshold")
    parser.add_argument("--stdio", action="store_true", help="Stdio protocol mode")
    parser.add_argument("--selftest", action="store_true", help="Run self-test suite")

    args = parser.parse_args()

    # Handle self-test
    if args.selftest:
        return selftest()

    # Handle stdio protocol
    if args.stdio:
        handle_stdio()
        return 0

    # Load configuration
    config = json.loads(json_dumps_canonical(DEFAULTS))
    if args.config:
        try:
            with open(args.config, "r") as f:
                user_config = json.load(f)
            deep_merge(config, user_config)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return 2

    # Override threshold if specified
    if args.threshold is not None:
        config["threshold"] = args.threshold

    # Read input
    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                markdown = f.read()
        except Exception as e:
            print(f"Error reading input: {e}", file=sys.stderr)
            return 2
    else:
        # Read from stdin
        markdown = sys.stdin.read()

    # Analyze
    try:
        slides = parse_slides(markdown)

        # Check cache
        cache_path = os.path.join(os.path.dirname(args.input) if args.input else ".", CACHE_FILE)
        cache = load_cache(cache_path)
        cached_results = get_cached_results(slides, cache)

        # Analyze uncached slides
        uncached_slides = [s for s in slides if not cached_results[s.uuid]]
        if uncached_slides:
            new_results = evaluate_all(tuple(uncached_slides), config)

            # Update cache - create UUID to result mapping
            new_results_map = {r.uuid: r for r in new_results}
            for slide in uncached_slides:
                result = new_results_map.get(slide.uuid)
                if result:
                    cache[slide.uuid] = {
                        "diagnostics": [vars(f) for f in result.diagnostics],
                        "score": result.score,
                        "bucket_scores": result.bucket_scores
                    }
                    cached_results[slide.uuid] = result

            save_cache(cache_path, cache)

        # Combine results in original order
        all_results = []
        for slide in slides:
            if cached_results[slide.uuid]:
                all_results.append(cached_results[slide.uuid])

        slide_results = tuple(all_results)

        # Calculate summary
        scores = [r.score for r in slide_results]
        avg_score = sum(scores) / len(scores) if scores else 0

        # Calculate bucket averages
        bucket_scores = {}
        for bucket in config["buckets"]:
            bucket_values = [r.bucket_scores.get(bucket, 100) for r in slide_results]
            bucket_scores[bucket] = sum(bucket_values) / len(bucket_values) if bucket_values else 100

        summary = {
            "total_slides": len(slides),
            "avg_score": round(avg_score, 1),
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "threshold": config["threshold"],
            "bucket_scores": bucket_scores,
            "passing": sum(1 for s in scores if s >= config["threshold"]),
            "total_issues": sum(len(r.diagnostics) for r in slide_results)
        }

        # Engine metadata
        engine_meta = {
            "version": "0.2.0",
            "config_checksum": sha1(json_dumps_canonical(config)),
            "rule_order": sorted([r.id for r in REGISTRY])
        }

        # Generate output
        if args.sarif:
            output = json.dumps(to_sarif(slide_results), indent=2)
        elif args.text:
            output = to_text(slide_results, summary)
        else:  # Default to JSON for agent-focused design
            output = json.dumps(to_json(slide_results, summary, engine_meta), indent=2)

        # Write output
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output)
        else:
            print(output)

        # Exit code based on threshold
        passing = avg_score >= config["threshold"]
        return 0 if passing else 1

    except Exception as e:
        print(f"Analysis error: {e}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())