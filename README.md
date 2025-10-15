# SlideGauge

[![Test](https://github.com/nibzard/slidegauge/actions/workflows/test.yml/badge.svg)](https://github.com/nibzard/slidegauge/actions/workflows/test.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Static analyzer for Marp Markdown decks** - validates slide quality with AI-agent-friendly feedback

SlideGauge is a zero-dependency Python tool that analyzes Marp markdown presentations and provides actionable feedback on slide quality. Designed specifically for AI coding agents to use when creating slides, but useful for humans too.

```bash
# Try it instantly with uvx (no installation)
uvx --from git+https://github.com/nibzard/slidegauge slidegauge example.md --text
```

## Features

- 🎯 **AI-Agent Optimized** - Clear, actionable diagnostics with specific suggestions
- 📊 **Comprehensive Analysis** - Content length, bullets, lines, colors, accessibility, code blocks
- 🎨 **Accessibility Checks** - WCAG contrast ratios, alt text validation
- 🚀 **Zero Dependencies** - Single Python file, no external packages
- ⚡ **Smart Caching** - UUID-based caching for fast re-analysis
- 🔧 **Multiple Formats** - JSON (default), SARIF, text output

## Quick Start

### Run with uvx (no installation)

```bash
# Analyze a presentation
uvx --from git+https://github.com/nibzard/slidegauge slidegauge presentation.md

# Get text summary
uvx --from git+https://github.com/nibzard/slidegauge slidegauge presentation.md --text

# JSON output (default)
uvx --from git+https://github.com/nibzard/slidegauge slidegauge presentation.md --json

# Try the included example
uvx --from git+https://github.com/nibzard/slidegauge slidegauge example.md --text
```

### Install with uv

```bash
# Install globally
uv tool install git+https://github.com/nibzard/slidegauge

# Use it
slidegauge presentation.md
```

### Traditional installation

```bash
# Clone and use directly
git clone https://github.com/nibzard/slidegauge.git
cd slidegauge
python3 slidegauge.py presentation.md

# Or install with pip
pip install git+https://github.com/nibzard/slidegauge
slidegauge presentation.md
```

## Usage

### Basic Analysis

```bash
slidegauge presentation.md
```

Output (JSON by default):
```json
{
  "slides": [
    {
      "uuid": "uuid5:...",
      "title": "Welcome",
      "metrics": {
        "title_length": 7,
        "content_chars": 245,
        "bullets": 5,
        "lines": 12,
        ...
      },
      "diagnostics": [
        {
          "rule": "content/too_long",
          "severity": "warning",
          "message": "Content 380 > max 350 (reduce by ~30 chars or split into 2 slides)",
          "deduction": 15
        }
      ],
      "score": 85
    }
  ],
  "summary": {
    "total_slides": 25,
    "avg_score": 88.2,
    "passing": 24,
    "threshold": 70
  }
}
```

### Text Summary

```bash
slidegauge presentation.md --text
```

Output:
```
Slide 1 (✓ 100) • no issues
Slide 2 (✓ 85) • content/too_long(15)
Slide 3 (✓ 90) • bullets/too_many(10)
...
SUMMARY: avg=88.2 • passing=24/25 • threshold=70
```

### SARIF Format

```bash
slidegauge presentation.md --sarif > results.sarif
```

Perfect for CI/CD pipelines and GitHub Code Scanning.

## Rules & Scoring

SlideGauge checks **11 rules** across **5 categories**:

### Content Rules
- ✅ **title/required** - Every slide needs a title (# or ##)
- ⚠️ **title/too_long** - Titles should be ≤35 chars
- ⚠️ **content/too_long** - ≤350 chars (≤450 for exercises)
- ℹ️ **content/too_short** - Add context if <50 chars
- ⚠️ **bullets/too_many** - Max 6 bullets per slide
- ⚠️ **lines/too_many** - Max 15 lines per slide

### Accessibility Rules
- ✅ **accessibility/alt_required** - All images need alt text
- ℹ️ **links/bare_urls** - Format URLs as `[text](url)`

### Color Rules
- ✅ **color/low_contrast** - WCAG AA: ≥4.5:1 contrast
- ⚠️ **color/too_many** - Max 6 unique colors

### Code Rules
- ⚠️ **code/too_long** - ≤10 lines simple code, ≤5 complex

### Scoring
- Each slide starts at 100 points
- Rule violations deduct points (see weights in config)
- Default threshold: 70 points to pass
- Bucket scores: `a11y`, `code`, `color`, `content`, `layout`

## Configuration

### Per-Slide Overrides

Disable rules with comments:
```markdown
<!-- slidegauge: disable content/too_long -->
## My Long Slide

Lots of content here...
```

### Custom Config File

```json
{
  "threshold": 80,
  "rules": {
    "content": {
      "max_chars": 400,
      "max_bullets": 8
    }
  },
  "weights": {
    "content/too_long": 10
  }
}
```

```bash
slidegauge presentation.md --config myconfig.json
```

## AI Agent Usage

### Stdio Protocol

For programmatic use by AI agents:

```python
import json
import subprocess

# Start slidegauge in stdio mode
proc = subprocess.Popen(
    ['slidegauge', '--stdio'],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

# Send analyze request
request = {
    "op": "analyze",
    "document": "# My Slide\nContent here...",
    "config": {}
}
proc.stdin.write(json.dumps(request) + '\n')
proc.stdin.flush()

# Get response
response = json.loads(proc.stdout.readline())
print(response['result']['summary']['avg_score'])
```

### Operations
- `analyze` - Full analysis with scores
- `slides` - Quick parse without analysis
- `rules` - List all available rules
- `explain` - Get rule documentation

## Examples

### Example Output for AI Agent

```json
{
  "rule": "content/too_long",
  "severity": "warning",
  "message": "Adjusted content 380 > max 350 (reduce by ~30 chars or split into 2 slides)",
  "deduction": 15
}
```

**AI Agent Action:** Sees specific guidance to reduce by 30 chars or split, can act accordingly.

### Example Output for Humans

```
Slide 5 (✓ 75) • content/too_long(15), lines/too_many(10)
```

**Quick scan:** Score 75, needs condensing

## Development

### Run Self-Tests

```bash
slidegauge --selftest
```

### Architecture

- **Zero dependencies** - Pure Python 3.8+ stdlib only
- **Single file** - ~1000 lines, easy to audit
- **Deterministic** - Same input → same output (UUID-based caching)
- **Extensible** - Simple rule registry pattern

### Adding Rules

```python
@register
class MyRule(Rule):
    """Helpful description for AI agents"""
    id = "category/rule_name"
    severity = "warning"
    bucket = "content"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        if slide.metrics["something"] > threshold:
            return (Finding(
                self.id,
                self.severity,
                "Clear message with (actionable suggestion)",
                deduction=cfg["weights"][self.id]
            ),)
        return ()
```

## Why SlideGauge?

**For AI Agents:**
- Get immediate, actionable feedback when generating slides
- No ambiguous errors - every message includes what to do
- JSON output with structured diagnostics
- Caching prevents redundant work

**For Humans:**
- Catch common slide design issues early
- Ensure accessibility standards
- Maintain consistent presentation quality
- Quick text output for CLI workflows

## Contributing

This tool was built to help AI coding agents create better Marp presentations. Contributions welcome!

## License

MIT

## Credits

Built with ❤️ for the AI agent ecosystem. Tested with Sourcegraph Cody and Amp.
