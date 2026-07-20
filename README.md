# Jonah Krier Portfolio

Static photography portfolio built with plain HTML, CSS, and JavaScript, with an
optional tag-driven darktable export pipeline.

The site is intentionally lightweight: there is no framework, package manager, or build step.

## Pages

- `index.html` - homepage carousel
- `selected-works.html` - selected works carousel, thumbnail grid, and lightbox
- `info.html` - about/contact page

## Important files

- `data/projects.js` - generated content data for homepage and project images
- `script.js` - renders carousels, thumbnails, lightbox, counters, and project dropdowns
- `styles.css` - all layout and visual styling
- `Images/` - all image assets
- `automation/` - darktable sync, safety configuration, tests, and task installer

Once the automation is initialized, image/project edits start by tagging or
untagging in darktable. See [`automation/README.md`](automation/README.md). The
manual instructions below remain useful as a fallback before activation.

## Automated darktable workflow

The supported tags are:

- `portfolio|home`
- `portfolio|info` (exactly one image)
- `portfolio|project|selected-works`
- `portfolio|project|<future-project-slug>`

Validate the selections without changing the site:

```powershell
.\automation\Sync-Portfolio.cmd -DryRun
```

The pipeline refuses to change the site if the tag namespace is uninitialized,
the Info selection is not exactly one image, a required collection is empty, the
archive identity/source files are unavailable, or the catalog changes during an
export. Generated AVIF filenames are content-hashed, and stale cleanup is limited
to files owned by the last successful manifest after a cache-safe grace period.

## Local preview

Open `index.html` in a browser.

If browser caching makes image changes hard to see, refresh with cache disabled or use a private/incognito window.

## Image structure

```txt
Images/
  Home/
    Index_01_....avif

  Info/
    Info_001_....avif

  SelectedWorks/
    Carousel/
      SW_Carousel_01_....avif
    Grid/
      SW_Grid_01_....avif
    Lightbox/
      SW_Lightbox_01_....avif
```

### What each folder does

- `Images/Home/` - homepage autoplay carousel
- `Images/Info/` - about/contact page image
- `Images/SelectedWorks/Carousel/` - selected works inline carousel images
- `Images/SelectedWorks/Grid/` - smaller thumbnail grid images
- `Images/SelectedWorks/Lightbox/` - full lightbox images

## The data file

The central content file is:

```txt
data/projects.js
```

It defines:

- homepage carousel images
- project titles
- project page URLs
- project slugs
- project image order
- carousel image paths
- grid image paths
- lightbox image paths

The site uses this file to generate:

- the homepage carousel
- project carousel images
- thumbnail grid images
- lightbox image paths
- image counters
- the Projects dropdown navigation

## Replacing an existing image

If the filename stays the same, no code changes are needed.

For a Selected Works image, replace the matching files in:

```txt
Images/SelectedWorks/Carousel/
Images/SelectedWorks/Grid/
Images/SelectedWorks/Lightbox/
```

Example:

```txt
Images/SelectedWorks/Carousel/SW_Carousel_15_DSC07873.avif
Images/SelectedWorks/Grid/SW_Grid_15_DSC07873.avif
Images/SelectedWorks/Lightbox/SW_Lightbox_15_DSC07873.avif
```

The `Carousel` version is used for the inline carousel.  
The `Grid` version is used for the thumbnail grid.  
The `Lightbox` version is used when a thumbnail is opened.

## Adding a new image to an existing project

1. Add the carousel version to the project `Carousel/` folder.
2. Add the smaller thumbnail version to the project `Grid/` folder.
3. Add the lightbox version to the project `Lightbox/` folder.
4. Open `data/projects.js`.
5. Find the project's `images: [...]` list.
6. Copy an existing image object.
7. Paste it where the new image should appear.
8. Update:
   - `main`
   - `grid`
   - `lightbox`
   - `alt`

Example image object:

```js
{
  main: "Images/SelectedWorks/Carousel/SW_Carousel_21_FILENAME.avif",
  grid: "Images/SelectedWorks/Grid/SW_Grid_21_FILENAME.avif",
  lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_21_FILENAME.avif",
  alt: "Photograph 21"
}
```

If `grid` is present, the thumbnail grid uses it. If `grid` is omitted, the grid falls back to `main`.

The image order in `data/projects.js` controls:

- carousel order
- thumbnail grid order
- lightbox order
- image counter order

## Adding a new project page

This is the current low-code workflow.

### 1. Add image folders

Create folders like:

```txt
Images/NewProject/Carousel/
Images/NewProject/Grid/
Images/NewProject/Lightbox/
```

Add the project images to those folders.

### 2. Add project data

Open:

```txt
data/projects.js
```

Copy the existing Selected Works project object:

```js
{
  title: "Selected Works",
  slug: "selected-works",
  page: "selected-works.html",
  images: [
    ...
  ],
}
```

Paste it after the existing project object, then change:

- `title` - the label shown in the Projects dropdown
- `slug` - the project id used by the HTML page
- `page` - the new HTML file
- `images` - the new project image paths

Example:

```js
{
  title: "New Project",
  slug: "new-project",
  page: "new-project.html",
  images: [
    {
      main: "Images/NewProject/Carousel/NP_Carousel_01_FILENAME.avif",
      grid: "Images/NewProject/Grid/NP_Grid_01_FILENAME.avif",
      lightbox: "Images/NewProject/Lightbox/NP_Lightbox_01_FILENAME.avif",
      alt: "Photograph 1"
    }
  ],
}
```

### 3. Create the HTML page

Copy:

```txt
selected-works.html
```

Rename the copy, for example:

```txt
new-project.html
```

In the copied file, update the body tag:

```html
<body class="selected-works-page" data-project="new-project">
```

The `data-project` value must match the `slug` in `data/projects.js`.

### 4. Update the page title

In the new HTML page, update:

```html
<title>New Project - Jonah Krier</title>
```

### 5. Check the navigation

The Projects dropdown is generated from `data/projects.js`, so the new project should appear automatically.

## Updating homepage images

1. Add/replace files in:

```txt
Images/Home/
```

2. Open `data/projects.js`.
3. Edit the `home.images` list.

Example:

```js
{ src: "Images/Home/Index_01_FILENAME.avif", alt: "Photograph 1" }
```

## Updating the info page image

The info page image is currently referenced directly in:

```txt
info.html
```

Look for:

```html
<img src="Images/Info/Info_001_DSC02620.avif" alt="Photograph by Jonah Krier" />
```

Replace the file path if needed.

## Deployment to Vercel

Import the GitHub repository into Vercel.

Suggested settings:

- Framework preset: `Other`
- Build command: leave empty
- Output directory: leave empty / project root
- Install command: leave empty

Vercel will serve `index.html` as the root page.

## Pre-deploy checklist

- Open `index.html` locally.
- Check homepage carousel.
- Check `selected-works.html`.
- Check carousel next/previous click areas.
- Check thumbnail grid.
- Check lightbox on desktop and mobile.
- Check `info.html`.
- Confirm all new images are committed to GitHub.
