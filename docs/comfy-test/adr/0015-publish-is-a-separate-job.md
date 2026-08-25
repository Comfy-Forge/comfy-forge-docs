# ADR-0015: Publish is a separate job, on the consumer's own gh-pages

**Status:** accepted (2026-08)

## Decision

> **Test jobs only upload a `results-<lane>` artifact. A separate job
> collects the artifacts and pushes a static dashboard to the consuming
> repository's own gh-pages.** comfy-test hosts nothing.

## Context

Two independent decisions, one shape.

**Why a separate job.** Publishing touches the network and a git branch:
tokens expire, `gh-pages` races between concurrent runs, GitHub has bad
minutes. If publish lives inside the test job, a flaky push fails a lane
whose tests passed, and re-running it repeats the slowest work in the repo
(a full install + execution) to retry a `git push`. Splitting means the
expensive stage produces an artifact once, and the cheap stage can be
re-run alone.

**Why the consumer's gh-pages.** The results describe *their* pack on
*their* commit. Hosting them centrally would make comfy-test an operator of
other people's data: an availability dependency, a cost centre, a privacy
question for private packs, and an authorisation problem (who may see whose
results). Pushing a static site into the repository that already owns the
code keeps the data next to its subject and inherits that repo's existing
permissions.

## Alternatives rejected

- **Publish in-job.** Couples a slow, expensive stage to a flaky network
  operation; re-running to fix a push re-runs the tests.
- **A hosted comfy-test dashboard service.** Requires running a service,
  storing other people's results, and answering for uptime and access
  control. Rejected as a scope explosion for a CI tool.
- **Artifacts only, no dashboard.** Honest and unusable: a multi-lane
  matrix produces a dozen zip files with no cross-lane view, which is
  the entire point of running a matrix.

## Consequences

- Consumers must enable gh-pages and supply a token with write access; the
  matrix workflow takes `publish: false` for repositories that do not want
  it.
- The dashboard is **static HTML generated at publish time**. It cannot
  query, filter server-side or update after the fact -- regeneration means
  re-publishing.
- Results are as public as the repository. A private pack's results stay
  private; a public pack's results are public, including logs and
  screenshots. That is a disclosure surface consumers should know about.
- Per-branch dashboards exist because branches produce different results
  for the same pack; the publish step namespaces by branch.
- Because publishing is decoupled, a partially-failed matrix still produces
  a dashboard from the artifacts that did upload -- missing cells rather
  than no page.
