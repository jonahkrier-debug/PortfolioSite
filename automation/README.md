# darktable portfolio automation

This pipeline makes darktable tags the source of truth for the portfolio. It reads
the live darktable catalog from `%LOCALAPPDATA%\darktable`, exports selected RAWs
from the guarded `F:\7_Photo` archive, updates the static site, and can run every
few minutes through Windows Task Scheduler.

Nothing is installed automatically. The installer first requires a successful dry
run, so the existing site cannot be interpreted as an empty portfolio before the
tags are initialized.

## Tags

Attach these hierarchical tags in darktable:

- `portfolio|home` — homepage carousel; at least one image is required.
- `portfolio|info` — About / Contact image; **exactly one** image is required.
- `portfolio|project|selected-works` — the existing Selected Works project; at
  least one image is required.
- `portfolio|project|<slug>` — any future project, where `<slug>` is lowercase
  kebab-case such as `city-at-night`.

One image may have any combination of Home, Info, and project tags. Project tags
are discovered from the prefix, so a new valid tag automatically produces its
image folders, data entry, navigation link, and `<slug>.html` page. Add an entry
to `projects` in `portfolio-sync.json` only when a custom title, description, or
page filename is wanted.

Images within each tag are ordered by darktable's tag-association position. For a
deliberate sequence, open the **exact** Home or project tag as the collection (not
a wildcard/prefix collection), choose **Custom sort**, set it to **Ascending**, and
drag the thumbnails into reading order. The pipeline always reads the stored
positions from low to high, regardless of darktable's current display-direction
toggle. In darktable's icon, Ascending is the fixed downward arrow beside bars
that grow from short at the top to long at the bottom; the arrow itself does not
change between Ascending and Descending.

A batch tag follows the current darktable collection order; detaching and
reattaching a tag moves that image to the end of that tagged sequence. Home and
each project have independent orders. Changing only this sequence updates site
data while reusing the existing rendered images.

The selected catalog records must resolve beneath `F:\7_Photo`. This intentionally
rejects duplicate catalog records that point at temporary or disconnected `C:` or
`D:` locations.

## First-time setup

1. In darktable, attach all three required tags above to the intended images.
2. Make sure `portfolio|info` is attached to one image only.
3. Close darktable once so all XMP sidecars are flushed, then run:

   ```powershell
   .\automation\Sync-Portfolio.cmd -DryRun
   ```

4. Inspect the reported counts. Run one manual sync:

   ```powershell
   .\automation\Sync-Portfolio.cmd
   ```

5. Preview the site and commit the new automation/generated files.
6. Install the two-minute local sync task:

   ```powershell
   .\automation\Install-PortfolioSyncTask.cmd
   ```

To also commit and push each successful change to `origin/main`, explicitly opt in:

```powershell
.\automation\Install-PortfolioSyncTask.cmd -EnableGitPublish
```

Automatic Git publishing requires a clean `main` worktree, `main` aligned with its
`origin/main` tracking ref, and working non-interactive Git credentials. If a push
fails after a local commit, push or reconcile that commit manually before the task
will publish again. Local-only sync is the safer default.

## Normal workflow

Tag or untag images in darktable. The installed task notices the change within two
minutes. There is no site data file to edit and no export button to press.

When replacing the Info image, attach `portfolio|info` to the new image and detach
it from the old image promptly. If the task observes the temporary zero/two-image
state, it rejects that run and preserves the last good site; the next valid run
succeeds normally.

Useful commands:

```powershell
# Validate counts, paths, sidecars, and archive identity without exporting
.\automation\Sync-Portfolio.cmd -DryRun

# Sync immediately
.\automation\Sync-Portfolio.cmd

# View task state
Get-ScheduledTask -TaskName "Darktable Portfolio Sync"

# Remove only the scheduled task (generated site files remain)
.\automation\Uninstall-PortfolioSyncTask.cmd
```

Logs are written to `.portfolio-sync\portfolio-sync.log`.

## Safety model

- The catalog databases and RAW/XMP sources are opened read-only.
- The `portfolio` namespace must exist; an absent namespace is never treated as
  an empty desired site.
- The Info tag must resolve to exactly one distinct image, and required collections
  may not be empty.
- The `F:` volume label and volume GUID must match the configured archive, and
  sentinel paths plus every selected RAW/XMP must exist.
- A filesystem/process lock prevents concurrent sync instances.
- Every export is staged and checked as AVIF before publication.
- The catalog, tag ordering, RAW metadata, and XMP hash are re-read before commit.
  If anything changed during export, the staged build is discarded.
- Published filenames contain a hash of the rendered AVIF bytes, preventing stale
  browser/CDN cache reuse after an edit.
- `data/projects.js` is explicitly revalidated by browsers, while hashed generated
  images are served as immutable. Removed images/pages remain available for a
  14-day cache grace period before managed cleanup.
- Only files recorded in the last successful manifest (or explicitly referenced
  legacy image roots during the first migration) can be retired. Stale files are
  moved transactionally and restored if publication fails.
- A disconnected/wrong archive, locked catalog, mid-run source change, corrupt
  export, or invalid manifest leaves the last good site in place.

The export command follows darktable's documented CLI/XMP workflow. Version-zero
edits use `<filename>.xmp`; darktable duplicates use
`<basename>_<version>.<extension>.xmp`.

## Configuration

Machine paths, archive identity, tag names, required projects, export dimensions,
AVIF settings, and the stale-file grace period live in
`automation/portfolio-sync.json`. If Windows assigns the archive a new drive letter
or volume GUID, update the guard intentionally rather than weakening it.

Generated state:

- `Images/Generated/` — content-addressed AVIF assets.
- `data/projects.js` — generated browser data.
- `data/portfolio-manifest.json` — last successful ownership/cache manifest.
- `selected-works.html` and future project pages — generated from
  `automation/templates/project.html.template`.
- `index.html` and `info.html` — only their hard-coded fallback/Open Graph image
  references are updated.
