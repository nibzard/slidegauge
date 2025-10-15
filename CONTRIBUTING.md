# Contributing to SlideGauge

Thank you for considering contributing to SlideGauge! This project aims to help AI coding agents and humans create better Marp presentations.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/nibzard/slidegauge.git
cd slidegauge

# Run tests
python3 slidegauge.py --selftest

# Test on example
python3 slidegauge.py example.md --text
```

## Project Philosophy

1. **AI-First Design** - Every diagnostic message should be actionable for an AI agent
2. **Zero Dependencies** - Keep it simple, pure Python stdlib only
3. **Deterministic** - Same input → same output, always
4. **Single File** - Easy to audit, easy to vendor

## How to Contribute

### Reporting Bugs

Found a bug? Great! Please include:

1. **Example markdown** that triggers the bug
2. **Expected behavior** vs actual behavior
3. **SlideGauge version** (`slidegauge.py` line 574)
4. **Python version** (`python3 --version`)

Example:
```markdown
**Bug:** Title detection fails on malformed markdown

**Input:**
\`\`\`markdown
# Title## Subtitle on same line
\`\`\`

**Expected:** Title = "Title"
**Actual:** Title = "Title## Subtitle on same line"

**Version:** SlideGauge 0.2.0, Python 3.11.5
```

### Suggesting Features

Feature ideas welcome! Please explain:

1. **Use case** - Why is this needed?
2. **AI agent benefit** - How does it help AI agents?
3. **Example** - Show input/output

Example:
```markdown
**Feature:** Detect speaker notes

**Use case:** Speaker notes should not count toward content limits

**AI benefit:** Agents can add detailed notes without triggering length warnings

**Example:**
\`\`\`markdown
## Slide Title
Content here

<!-- Speaker notes: detailed context -->
\`\`\`
```

### Adding Rules

Want to add a new rule? Follow this pattern:

```python
@register
class MyNewRule(Rule):
    """Clear description of what this checks and why it matters"""
    id = "category/rule_name"
    severity = "warning"  # or "error" or "info"
    bucket = "content"    # or "code", "a11y", "color", "layout"

    def check(self, slide: Slide, cfg: dict) -> Tuple[Finding, ...]:
        # Check condition
        if slide.metrics["something"] > threshold:
            excess = slide.metrics["something"] - threshold
            return (Finding(
                self.id,
                self.severity,
                f"Diagnostic message with specific numbers and (actionable suggestion)",
                deduction=cfg["weights"][self.id]
            ),)
        return ()
```

**Rule Guidelines:**
- Docstring explains WHY the rule exists
- Message includes specific numbers (how much over/under)
- Message includes actionable suggestion in parentheses
- Use clear, non-technical language
- Default weight in `DEFAULTS` dict

**Add test case in selftest():**
```python
# Test N: Description
markdown_n = "# Test\nContent that triggers rule"
slides_n = parse_slides(markdown_n)
results_n = evaluate_all(slides_n, cfg)
assert_true(
    any(f.rule == "category/rule_name" for r in results_n for f in r.diagnostics),
    "Should detect rule violation"
)
```

### Adding Metrics

Want to track a new metric? Update `scan_slide()`:

```python
def scan_slide(text: str, cfg: dict) -> dict:
    metrics = {
        # ... existing metrics ...
        "your_new_metric": 0,  # Add here
    }
    
    # Scan content
    for i, line in enumerate(lines):
        # ... existing logic ...
        
        # Your detection logic
        if some_condition:
            metrics["your_new_metric"] += 1
    
    return metrics
```

**Then use it in a rule:**
```python
if slide.metrics["your_new_metric"] > threshold:
    # trigger finding
```

### Improving Messages

Current messages not helpful enough? Submit a PR with better wording!

**Before:**
```python
f"Content {val} > max {limit}"
```

**After:**
```python
excess = val - limit
f"Content {val} > max {limit} (reduce by ~{excess} chars or split into 2 slides)"
```

## Code Style

- **Keep it simple** - Readable > clever
- **Type hints** - Use them (`Tuple`, `List`, `Dict`, `Optional`)
- **Docstrings** - For classes and complex functions
- **No external deps** - Stdlib only
- **Deterministic** - Use tuples for collections, sort when needed

## Testing

### Run Self-Tests

```bash
python3 slidegauge.py --selftest
```

All tests must pass before submitting a PR.

### Test on Examples

```bash
# Test on comprehensive example
python3 slidegauge.py example.md --text

# Should show various issues and scores
```

### Manual Testing

Create a test markdown file with your change:

```bash
cat > test.md << 'EOF'
# Test Slide
Your test content here
EOF

python3 slidegauge.py test.md --json
```

## Pull Request Process

1. **Fork** the repo
2. **Create branch** from `main`: `git checkout -b feature/my-improvement`
3. **Make changes** following guidelines above
4. **Test** with `--selftest` and `example.md`
5. **Commit** with clear message: `git commit -m "Add rule for X: fixes #123"`
6. **Push** to your fork: `git push origin feature/my-improvement`
7. **Open PR** with description of changes

### PR Checklist

- [ ] All self-tests pass
- [ ] Example.md still works
- [ ] New rules have docstrings
- [ ] New rules have test cases
- [ ] Messages are actionable
- [ ] No external dependencies added
- [ ] Version bump if needed (in `slidegauge.py` line 574)

## Versioning

We use semantic versioning (semver):

- **Major** (1.0.0): Breaking changes to API or output format
- **Minor** (0.2.0): New features, new rules
- **Patch** (0.2.1): Bug fixes, message improvements

Update version in:
- `slidegauge.py` line 574 (`"version": "0.2.0"`)
- `pyproject.toml` line 2 (`version = "0.2.0"`)

## Release Process

Maintainers will:
1. Merge PR to `main`
2. Tag release: `git tag v0.2.1 && git push --tags`
3. GitHub Actions creates release (future)

## Questions?

- **Open an issue** for discussions
- **Check existing issues** for similar questions
- **Read the code** - it's ~1000 lines, well-commented

## Recognition

Contributors will be:
- Listed in CONTRIBUTORS.md
- Mentioned in release notes
- Thanked in commit messages

Thank you for making SlideGauge better! 🎉
