# OfferPilot — An Open-source, Local-first Job-search Agent

[简体中文](README.md) | [English](README.en.md)

**Prepare for your next interview—and explore how an agent gets things done.**

OfferPilot by offercontext brings applications, resumes, interviews and offers into one workspace.
Its built-in Pilot agent uses your saved job-search materials to find information, draft content and call tools.
Changes to important job-search records require your confirmation by default. It does not automatically apply for jobs or contact recruiters.

For AI builders, the same project offers illustrated scenarios, small experiments and source walkthroughs
covering tool use, context, write approval and execution control.

[Windows unsigned test build](https://github.com/offercontext/offerPilot/releases/tag/v0.1.0-desktop.1) · [Docker / source setup](#getting-started) · [Explore the agent](#use-it-and-learn-how-it-works) · [User guide (Chinese)](docs/product-manual/产品说明书.md)

The Windows installer is an experimental prerelease; read the [test-build limitations](#getting-started) before installing.
Basic application tracking does not need AI. AI features use your configured model service and may send relevant data off-device and incur provider fees.
This English README does not imply that every product screen or tutorial is available in English.

## Watch an interview become a saved record

These screenshots come from a real operation captured on October 8, 2026, using a real model and isolated fictional data
at source revision `c0a447bb`. They are not a full acceptance test of the current Windows release.
The company, role and date are demonstration data, not a pending task.

The request was to add an online, 60-minute interview for the fictional Yunlan Data test-development role,
starting at 15:00 Beijing time (UTC+8) on October 15, 2026.

**Find the application → propose the details → you review and confirm → save → verify the calendar record.**

1. Pilot uses a tool to find the application and proposes an interview using the returned information.
2. The confirmation card shows what would be saved. No interview has been added yet.
3. After approval, the program saves the interview. The calendar lets you check the company, role and time. No notification was sent to a recruiter.

![Actual confirmation card for the fictional interview: October 15, 15:00, online, 60 minutes; interface in Chinese](docs/learn/images/runtime-20261008/05-interview-pending.jpg)

![Actual calendar record after confirmation: the same fictional interview, 15:00–16:00; interface in Chinese](docs/learn/images/runtime-20261008/08-calendar-verified.jpg)

“Done” is not enough: check the saved record.
Read the [full walkthrough](docs/learn/01-record-an-interview.md) and [capture conditions and verification scope](docs/learn/images/runtime-20261008/README.md) (Chinese).

## Getting started

A [Windows x64 unsigned test build (v0.1.0-desktop.1)](https://github.com/offercontext/offerPilot/releases/tag/v0.1.0-desktop.1) is available. You can also run OfferPilot locally with Docker or from source.

### Windows x64 desktop test build (unsigned)

This is an experimental prerelease, not a stable production release. Python, Node.js and Docker are not required for the installer.

1. Under the Release's Assets, download `OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe` and its matching `.sha256` file. The automatically generated Source code archives are not installers.
2. Before installing, run `Get-FileHash .\OfferPilot-Desktop-0.1.0-desktop.1-win-x64-setup.exe -Algorithm SHA256` in PowerShell and compare it with the checksum file and Release notes. A hash checks integrity; it does not authenticate the publisher.
3. After verifying the source and hash, open the installer, choose an installation folder, then launch the desktop shortcut. This unsigned build may trigger Windows SmartScreen or an unknown-publisher warning. If your browser, Windows or security software blocks the download or execution, stop and report it; do not disable protection or bypass warnings.
4. Start with test data. Before installing another version, fully quit the app and back up `%APPDATA%\OfferPilot Desktop\`. This workspace is separate from the source edition's `~/.offerpilot`; migration is not automatic and upgrade/downgrade compatibility is not guaranteed. Uninstalling retains app data by default, which is not a substitute for a backup.

The release update channel is not enabled; in-app update checks, downloads and installation are unavailable. Get future test builds manually from Releases. Real QQ mailbox setup and resumed mailbox reading are disabled in this EXE; manual email pasting and suggestion review remain available. AI features require your own model service and may transmit relevant data and incur provider fees.

See the Release notes for build provenance, checksums and validation scope, plus the [desktop validation notes](docs/architecture/desktop-validation.md) and [update boundaries](docs/architecture/desktop-updates.md) (Chinese).

### Docker or source

See the [installation instructions](README.md#快速开始)
and [user guide in Chinese](docs/product-manual/产品说明书.md).

Basic application tracking does not require AI configuration.

## Connect DSH and other external agents

Install the source edition's `mcp` extra to expose scoped application/event reads and interview proposals over stdio MCP.
Connections default to read-only. Scheduling needs an explicit grant and a separate user review in a trusted terminal;
the MCP surface has no approval tool. DSH uses its generic MCP client, with no OfferPilot native plugin required.
See the [setup and local trust boundaries (Chinese)](docs/architecture/external-agent-mcp.md).

## How it helps during your job search

| Feature | Purpose |
| --- | --- |
| Pilot agent | Query saved information, draft content and use tools, with confirmation before important record changes |
| Application tracking | Organize companies, roles, stages and interview schedules |
| Resume and material preparation | Maintain resume versions and review job-specific suggestions |
| Mock interviews | Practice with your resume and job description, then review feedback |
| Interview reviews | Record lessons and choose priorities for further practice |
| Offer comparison and negotiation preparation | Compare known terms and deadlines, then prepare questions and drafts |

See the [real interface screenshots](README.md#真实界面) and [user guide](docs/product-manual/产品说明书.md) (Chinese).
This repository describes the locally deployed edition. [offerContext Hub](https://hub.offercontext.cn) is a separate online service;
its features and data handling follow its own disclosures, and should not be assumed identical or connected.

## Haru and Pilot

Pilot is the built-in agent. Haru is an optional character entry point on wide desktop layouts.
You can hide Haru and keep using Pilot. Haru is an official Live2D sample character, not an original OfferPilot character;
see the [third-party notices](README.md#第三方角色与运行时).

## Use it, and learn how it works

You do not need to study agent engineering to use the workspace. AI builders can start with one concrete question:
**why should confirming the same interview twice still create only one record?**

- **Understand:** follow [one interview being recorded](docs/learn/01-record-an-interview.md), then the [four-article short route](docs/learn/README.md). No installation or coding is required to read it.
- **Experiment:** try the [Python standard-library experiment](docs/learn/examples/README.md): zero records before confirmation, one after approval, still one after repeated confirmation. It uses a simulated model, needs no model key and does not evaluate real model quality.
- **Read the implementation:** explore the [Harness source walkthroughs](docs/learn/advanced/README.md), including the agent loop, context, approval, cancellation, recovery and validation.

These learning materials are in Chinese and state their source revision and verification limits.
Reader trials have not yet been conducted; this is a reading and experimentation route, not a guarantee of mastering a complete system.

## Contribute

[Report an issue](https://github.com/offercontext/offerPilot/issues) or read the [contribution guide](CONTRIBUTING.md)
before proposing a change. Describe the goal, scope and checks you actually ran.

## Privacy and limitations

- Workspace data is stored locally. AI features send relevant materials to your configured model service and may incur provider fees. Local-first does not mean all AI processing stays on your device.
- Changes to important job-search records require confirmation by default. Review the confirmation card before proceeding.
- OfferPilot does not automatically submit applications or message recruiters. Review generated content, dates, numbers and commitments before using them.
- Interview recordings remain in the current page and are not uploaded or persisted. The optional offline Whisper model downloads to the browser cache only after you request it.
- AI can make mistakes. Decisions about applications, offers and negotiation remain yours.

## License

[AGPLv3](LICENSE) covers the project, not the Live2D character, model data or runtime.
Haru and Cubism Core belong to Live2D Inc.; their separate terms and the offline speech-model notices are in the
[third-party notices](README.md#许可证).
