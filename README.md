# Free-Merge Allowlist

Hash allowlist for Vertex Hub's **free merge tier**: merges consisting entirely
of one publisher's unmodified maps run without a subscription and without
credits. Identity is established by content, never by names — the API compares
the SHA-256 of every uploaded file against this list. Renamed resources,
modified files, unknown files, or a mix of two publishers always classify as a
normal paid merge.

## Layout

```
index.txt                       one repo-relative hash-file path per line
publishers/<name>/<map>.txt     one lowercase hex SHA-256 per line
```

`#` comment lines and blank lines are allowed everywhere. The publisher name is
the second path segment (`publishers/prompt/...` → publisher `prompt`).

A deliberate cross-publisher deal ("maps from A and B merge free together") is
just another folder containing the union of both catalogs, e.g.
`publishers/prompt-x-somedev/`. Delete the folder to revoke the deal.

## Rules

- **Append-only. Never delete hashes.** Map updates *add* the new file hashes;
  customers on old versions keep matching forever. The API tolerates stale
  copies precisely because the list only grows.
- **Write access is the entire security model.** Anyone who can push here can
  grant free merges against Vertex Hub infrastructure. Protected branch,
  maintainers only, no external merge requests. Partners email their release
  zips (or hashes) — they never push. Public *read* is harmless: hashes cannot
  be reversed or forged.
- Hash the **exact shipped release artifact**. Re-exporting a file changes its
  bytes and therefore its hash — republish after any re-export.

## Publishing a release

```powershell
# from a release zip
.\tools\publish.ps1 -Source "C:\releases\prompt_sandy_bank_v1.2.zip" -Publisher prompt

# from a resource folder, explicit list name
.\tools\publish.ps1 -Source "D:\maps\prompt_sandy_bank" -Publisher prompt -Name sandy_bank
```

The script hashes every `.ymap .ybn .ydr .ydd .ytyp` in the source (the file
types the merger API accepts for upload), appends only hashes not already
published for that publisher, and registers the hash file in `index.txt`.
Everything else (`.fxap`, textures, scripts, readmes) is ignored. Review the
diff, commit, push — done. Running it again with the same source is a no-op.

## How the API consumes this

The merger API reads the raw files over HTTP (no git client):
`<base-url>/index.txt`, then each listed file. Configure the base URL in the
service's `settings` table, key `merger/allowlist-url`, e.g.
`https://gitlab.com/vertex-hub/free-merge-allowlist/-/raw/main`. Refreshes
every 5 minutes; see `allowlist.service.ts` in `vertex-hub-merger-api`.
