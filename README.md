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

- **Only vanilla-named files are listed.** The merger merges duplicates of
  base-game files; a map's custom-named props never go through it. So the
  hashes cover the files whose name exists in the base game
  (`cdn.vertex-hub.com/gta5-resource-list.json`, the same inventory the app
  uses) plus **every lodlights ymap of any name**, because the LOD-light
  manager processes all of them. Both tools apply this filter by default.
- **Mapdata is not listed** (`cfx_prompt_*_mapdata`, the per-customer builds on
  GitHub). It is a merged product of its own with thousands of per-customer
  variants; free handling for it is a separate, name-based concern. The tools
  skip any path segment matching `*mapdata*` by default (`--skip`).

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
`https://raw.githubusercontent.com/Prompt-Coder/free-merge-allowlist/main`
(public read; the GitLab copy under vertex-hub is a private mirror). Refreshes
every 5 minutes; see `allowlist.service.ts` in `vertex-hub-merger-api`.

## Bulk backfill (Prompt Studio)

`tools/backfill.py` hashes a whole catalog in one run - release zips, resource
folders and the customer mapdata repositories (`Prompt-Coder/Sandy-Map-Data`,
`Prompt-Coder/Paleto-Map-Data`: every branch is one customer build, so every
blob reachable from any branch is a file a customer may hold):

```
python tools/backfill.py --publisher prompt --config tools/backfill-prompt.json
```

`tools/discover-prompt.py` writes `tools/backfill-prompt-all.json`: every folder
and zip whose name contains `prompt_` under the local server, dev and customer
pack roots, plus the group bundle zips. For bundles the `entry_include`
filter keeps only entries whose path contains `prompt_`, so third-party
resources packed next to ours never get listed.

```
python tools/discover-prompt.py --out tools/backfill-prompt-all.json
python tools/backfill.py --publisher prompt --config tools/backfill-prompt-all.json
```

Same rules as `publish.ps1`: append-only, publisher-wide dedup, `index.txt`
updated. Groups are written as `publishers/prompt/<group>.txt`, split into
`<group>-NN.txt` chunks of 50k hashes so each stays a small single HTTP fetch
for the API. Re-run after every release and whenever new mapdata branches were
generated; re-running with unchanged sources is a no-op.
