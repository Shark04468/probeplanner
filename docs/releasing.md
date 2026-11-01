# Paper Release Procedure

## Invariants

1. The release branch passes `npm run release:verify` and clean-cache reproduction.
2. An annotated tag `v0.2.1-paper` points to the tested commit and is never moved.
3. The GitHub Release is created from that existing tag.
4. Zenodo archives that GitHub Release; the Zenodo record version is `0.2.1-paper`.
5. The DOI landing page, GitHub tag, release asset `SOURCE_COMMIT.txt`, and manuscript all name the same commit.

## Publication order

```powershell
git status --short
python scripts/release/verify_release.py
git tag -a v0.2.1-paper -m "ProbePlanner paper release v0.2.1-paper"
git push origin main
git push origin v0.2.1-paper
```

Before pushing the tag, connect the public GitHub repository in Zenodo and enable it. Pushing the tag triggers `.github/workflows/paper-release.yml`, which verifies the tag, creates a Supplementary Data ZIP with `SOURCE_COMMIT.txt`, and publishes the GitHub Release. Zenodo then archives the release.

If a DOI must appear inside the tagged source, use the Zenodo deposition API to create a draft and reserve a DOI before the final commit. Add that reserved DOI to `CITATION.cff`, `.zenodo.json`, and the manuscript, rerun all release checks, commit, tag, upload the exact tagged source archive, and publish the deposition. Never amend or move the tag after DOI publication.

After Zenodo processing, record the version DOI and concept DOI in the manuscript. Verify the archived source SHA against:

```powershell
git rev-parse v0.2.1-paper^{commit}
gh release view v0.2.1-paper --json tagName,targetCommitish,url
```

Zenodo gives `.zenodo.json` precedence when both `.zenodo.json` and `CITATION.cff` are present, so both files must remain semantically consistent.

## Historical paper release

- Tag: `v0.2.0-paper`
- Commit: `0d5748e05c05e54d1330d05009747bcb4fa915a5`
- GitHub Release: `https://github.com/Shark04468/probeplanner/releases/tag/v0.2.0-paper`
- Zenodo version DOI: `10.5281/zenodo.21307547`
- Zenodo concept DOI: `10.5281/zenodo.21307546`
- Machine-readable lock: `docs/release/doi-lock.json`
