/*
  Portfolio content configuration
  --------------------------------
  This file is the main place to update image lists and project navigation.

  The site reads this data automatically to generate:
  - homepage carousel images
  - project carousel images
  - project thumbnail grids
  - lightbox image paths
  - image counters
  - the Projects dropdown menu

  Important:
  - Keep paths relative to the site root.
  - Use forward slashes `/`, even on Windows.
  - Keep commas after each image object.
  - For project pages, the `slug` here must match the HTML body attribute:
      <body data-project="selected-works">
*/

window.PORTFOLIO_DATA = {
  /*
    Homepage carousel
    -----------------
    To change homepage images:
    1. Add/replace files in Images/Home/
    2. Update this list in the order you want them to appear.

    Each homepage image needs:
    - src: path to the image file
    - alt: accessibility text; keep simple if no specific caption is needed
  */
  home: {
    images: [
      { src: "Images/Home/Index_01_DSC02069.avif", alt: "Photograph 1" },
      { src: "Images/Home/Index_02_DSC08253.avif", alt: "Photograph 2" },
      { src: "Images/Home/Index_03_DSC08249.avif", alt: "Photograph 3" },
      { src: "Images/Home/Index_04_DSC01651.avif", alt: "Photograph 4" },
      { src: "Images/Home/Index_05_R0000778.avif", alt: "Photograph 5" },
      { src: "Images/Home/Index_06_DSC00030.avif", alt: "Photograph 6" },
      { src: "Images/Home/Index_07_DSC07873.avif", alt: "Photograph 7" },
      { src: "Images/Home/Index_08_DSC01920.avif", alt: "Photograph 8" },
      { src: "Images/Home/Index_09_DSC02775.avif", alt: "Photograph 9" },
      { src: "Images/Home/Index_10_JonahKrierPortfolio-3.avif", alt: "Photograph 10" },
    ],
  },

  /*
    Project pages
    -------------
    Each object in this array is one project in the Projects dropdown.

    Project fields:
    - title: label shown in the Projects dropdown
    - slug: stable project id; must match the project page body `data-project`
    - page: HTML file for this project
    - images: ordered image list for carousel/grid/lightbox

    To add a new project:
    1. Copy the whole Selected Works project object below.
    2. Paste it after the closing `},` of Selected Works.
    3. Change `title`, `slug`, and `page`.
    4. Replace the image paths with the new project image paths.
    5. Create a matching HTML page copied from selected-works.html.
    6. In that HTML page, set:
         <body class="selected-works-page" data-project="your-new-slug">
  */
  projects: [
    {
      title: "Selected Works",
      slug: "selected-works",
      page: "selected-works.html",

      /*
        Selected Works images
        ---------------------
        Each image object needs:
        - main: image used for the inline carousel
        - lightbox: image used when the thumbnail opens in the lightbox
        - grid: optional smaller thumbnail/grid image; if omitted, `main` is used
        - alt: accessibility text

        The image order here controls:
        - carousel order
        - thumbnail grid order
        - lightbox order
        - image counter order

        To replace an existing image:
        - overwrite the file at the matching `main` path
        - overwrite the file at the matching `lightbox` path
        - overwrite the file at the matching `grid` path, if present
        - no code changes needed if the filenames stay the same

        To add a new image:
        - add files to Carousel/, Grid/, and Lightbox/
        - copy one image object below
        - update the paths and alt number/text

        Optional performance improvement:
        - If you export smaller thumbnail-only images later, add:
            grid: "path/to/your-grid-image.avif"
          The thumbnail view will use `grid`, while the carousel continues to use `main`
          and the lightbox continues to use `lightbox`.
      */
      images: [
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_01_DSC00030.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_01_DSC00030.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_01_DSC00030.avif", alt: "Photograph 1" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_02_DSC08253.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_02_DSC08253.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_02_DSC08253.avif", alt: "Photograph 2" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_03_DSC07582.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_03_DSC07582.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_03_DSC07582.avif", alt: "Photograph 3" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_04_DSC08249.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_04_DSC08249.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_04_DSC08249.avif", alt: "Photograph 4" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_05_DSC01651.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_05_DSC01651.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_05_DSC01651.avif", alt: "Photograph 5" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_06_DSC09343.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_06_DSC09343.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_06_DSC09343.avif", alt: "Photograph 6" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_07_DSC01953.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_07_DSC01953.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_07_DSC01953.avif", alt: "Photograph 7" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_08_DSC07911.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_08_DSC07911.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_08_DSC07911.avif", alt: "Photograph 8" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_09_DSC02398.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_09_DSC02398.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_09_DSC02398.avif", alt: "Photograph 9" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_10_DSC02296.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_10_DSC02296.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_10_DSC02296.avif", alt: "Photograph 10" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_11_DSC01306.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_11_DSC01306.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_11_DSC01306.avif", alt: "Photograph 11" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_12_DSC09695.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_12_DSC09695.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_12_DSC09695.avif", alt: "Photograph 12" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_13_DSC05299.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_13_DSC05299.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_13_DSC05299.avif", alt: "Photograph 13" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_14_R0000778.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_14_R0000778.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_14_R0000778.avif", alt: "Photograph 14" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_15_DSC07873.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_15_DSC07873.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_15_DSC07873.avif", alt: "Photograph 15" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_16_DSC01920.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_16_DSC01920.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_16_DSC01920.avif", alt: "Photograph 16" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_17_DSC02775.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_17_DSC02775.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_17_DSC02775.avif", alt: "Photograph 17" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_18_DSC09890.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_18_DSC09890.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_18_DSC09890.avif", alt: "Photograph 18" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_19_JonahKrierPortfolio-3.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_19_JonahKrierPortfolio-3.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_19_JonahKrierPortfolio-3.avif", alt: "Photograph 19" },
        { main: "Images/SelectedWorks/Carousel/SW_Carousel_20_DSC01353.avif", grid: "Images/SelectedWorks/Grid/SW_Grid_20_DSC01353.avif", lightbox: "Images/SelectedWorks/Lightbox/SW_Lightbox_20_DSC01353.avif", alt: "Photograph 20" },
      ],
    },
  ],
};
