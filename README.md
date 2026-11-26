# Scrapling Research Capture

A Codex skill for bounded, provenance-preserving capture of public research-related webpages, informed by the architecture of [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling).

It routes papers and scholarly metadata to existing academic APIs first, then uses ordinary HTTP or an allowed browser-rendering workflow only when a public source requires it. It stops at access controls, bot challenges, CAPTCHAs, or paywalls.

## Install

Copy this folder to your Codex skills directory, for example:

```text
~/.codex/skills/scrapling-research-capture/
```

This skill does not install the Scrapling Python package or browser dependencies.

## Contents

- `SKILL.md`: triggering conditions, collection boundaries, and workflow
- `references/project-analysis.md`: architecture review, adoption guidance, and official documentation links

