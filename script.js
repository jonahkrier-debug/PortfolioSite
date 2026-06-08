const siteData = window.PORTFOLIO_DATA || { home: { images: [] }, projects: [] };

// Project pages opt into their data by setting:
//   <body data-project="project-slug">
// The slug must match one project in data/projects.js.
const activeProject = siteData.projects.find((project) => project.slug === document.body.dataset.project);
const selectedWorksItems = activeProject?.images || [];
const homeItems = siteData.home?.images || [];

const viewButtons = document.querySelectorAll(".view-button");
const panels = document.querySelectorAll(".panel");
const menuToggle = document.querySelector(".menu-toggle");
const lightbox = document.querySelector(".lightbox");
const lightboxFrame = document.querySelector(".lightbox-frame");
const lightboxClose = document.querySelector(".lightbox-close");
const lightboxPrev = document.querySelector(".lightbox-prev");
const lightboxNext = document.querySelector(".lightbox-next");
let lightboxCount = document.querySelector(".lightbox-count");
const lightboxFocusAnchor = document.querySelector(".lightbox-focus-anchor");
const carouselAdvance = document.querySelector(".carousel-advance");
const projectCarouselFrame = document.querySelector(".sequence-panel .carousel-frame");
const homeCarouselFrame = document.querySelector(".home-carousel .carousel-frame");
const homeCarousel = document.querySelector(".home-carousel");
const carouselCount = document.querySelector(".carousel-count");
const thumbnailPanel = document.querySelector(".thumbnail-panel");

let thumbnailButtons = [];
let currentSequenceIndex = 0;
let isAnimating = false;
let currentHomeIndex = 0;
let isHomeAnimating = false;
let currentLightboxIndex = 0;
let isLightboxAnimating = false;
const preloadedImages = new Set();

renderProject(activeProject);
renderHomeCarousel();
renderProjectNav();

viewButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const nextView = button.dataset.view;

    viewButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    panels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.panel === nextView));
  });
});

if (menuToggle) {
  menuToggle.addEventListener("click", () => {
    const projectHeader = document.querySelector(".project-header");
    const isOpen = projectHeader.classList.toggle("is-open");

    menuToggle.setAttribute("aria-expanded", String(isOpen));
    menuToggle.classList.toggle("is-open", isOpen);
    document.body.classList.toggle("menu-open", isOpen);
  });
}

setupLightbox();
setupProjectCarousel();
setupHomeCarousel();
setupKeyboardControls();

function renderProject(project) {
  if (!project) {
    return;
  }

  // The first project image is placed into the static carousel shell.
  // After this, carousel movement uses the same project.images list.
  const firstImage = project.images[0];
  const carouselImage = projectCarouselFrame?.querySelector(".carousel-image");

  if (carouselImage && firstImage) {
    carouselImage.src = firstImage.main;
    carouselImage.alt = firstImage.alt;
  }

  if (carouselCount) {
    carouselCount.textContent = `1/${project.images.length}`;
  }

  if (lightboxCount) {
    lightboxCount.textContent = `1/${project.images.length}`;
  }

  if (thumbnailPanel) {
    // Thumbnail buttons are generated from data/projects.js.
    // Do not manually add thumbnail <button> elements in selected-works.html;
    // add image objects to the project data instead.
    thumbnailPanel.innerHTML = project.images
      .map(
        (image, index) => `
        <button type="button">
          <img src="${image.main}" data-full="${image.lightbox}" alt="Open photograph ${index + 1}" />
        </button>`
      )
      .join("");

    thumbnailButtons = Array.from(thumbnailPanel.querySelectorAll("button"));
  }
}

function renderProjectNav() {
  const dropdownMenus = document.querySelectorAll(".dropdown-menu");
  const projectLinks = document.querySelectorAll(".nav-dropdown > a");

  if (siteData.projects.length === 0) {
    return;
  }

  dropdownMenus.forEach((menu) => {
    // The Projects dropdown is generated from data/projects.js.
    // Adding a project object there automatically adds it to every dropdown.
    menu.innerHTML = siteData.projects
      .map((project) => {
        const isActive = project.slug === activeProject?.slug;
        return `<a class="${isActive ? "is-active" : ""}" href="${project.page}">${project.title}</a>`;
      })
      .join("");
  });

  projectLinks.forEach((link) => {
    link.classList.toggle("is-active", Boolean(activeProject));
    link.href = siteData.projects[0].page;
  });
}

function renderHomeCarousel() {
  const firstImage = homeItems[0];
  const carouselImage = homeCarouselFrame?.querySelector(".carousel-image");

  if (carouselImage && firstImage) {
    carouselImage.src = firstImage.src;
    carouselImage.alt = firstImage.alt;
  }
}

function setupLightbox() {
  if (!lightbox || !lightboxFrame || thumbnailButtons.length === 0) {
    return;
  }

  thumbnailButtons.forEach((button) => {
    const image = button.querySelector("img");

    button.addEventListener("pointerenter", () => preloadImage(getLightboxSource(image)));
    button.addEventListener("focus", () => preloadImage(getLightboxSource(image)));
    button.addEventListener("touchstart", () => preloadImage(getLightboxSource(image)), { passive: true });

    button.addEventListener("click", () => {
      currentLightboxIndex = thumbnailButtons.indexOf(button);
      lightboxFrame.classList.remove("is-loaded");
      lightboxFrame.innerHTML = `<img class="lightbox-image is-active" draggable="false" src="${getLightboxSource(
        image
      )}" alt="${image.alt.replace("Open ", "")}" /><span class="lightbox-count">${currentLightboxIndex + 1}/${
        thumbnailButtons.length
      }</span>`;
      lightboxCount = lightboxFrame.querySelector(".lightbox-count");
      const lightboxImage = lightboxFrame.querySelector(".lightbox-image");
      revealLightboxFrameWhenReady(lightboxImage);
      updateLightboxOrientation(lightboxImage);
      isLightboxAnimating = false;
      lightbox.showModal();
      document.body.classList.add("lightbox-open");
      lightboxFocusAnchor?.focus();
      preloadAdjacentLightboxImages();
    });
  });

  lightboxClose?.addEventListener("click", () => lightbox.close());
  lightbox.addEventListener("close", () => {
    document.body.classList.remove("lightbox-open");
  });
  lightbox.addEventListener("click", (event) => {
    if (event.target !== lightbox) {
      return;
    }

    const image = lightboxFrame.querySelector(".lightbox-image");
    const { left, right } = image.getBoundingClientRect();

    if (event.clientX < left) {
      showLightboxImage(currentLightboxIndex - 1);
    } else if (event.clientX > right) {
      showLightboxImage(currentLightboxIndex + 1);
    } else {
      lightbox.close();
    }
  });

  lightboxPrev?.addEventListener("click", () => {
    showLightboxImage(currentLightboxIndex - 1);
  });

  lightboxNext?.addEventListener("click", () => {
    showLightboxImage(currentLightboxIndex + 1);
  });

  lightboxFrame.addEventListener("click", (event) => {
    const { left, width } = lightboxFrame.getBoundingClientRect();
    const clickedOnLeftHalf = event.clientX < left + width / 2;

    showLightboxImage(currentLightboxIndex + (clickedOnLeftHalf ? -1 : 1));
  });
}

function setupProjectCarousel() {
  if (!carouselAdvance || selectedWorksItems.length === 0) {
    return;
  }

  carouselAdvance.addEventListener("click", (event) => {
    const { left, width } = carouselAdvance.getBoundingClientRect();
    const clickedOnLeftHalf = event.clientX < left + width / 2;

    showSequenceItem(currentSequenceIndex + (clickedOnLeftHalf ? -1 : 1));
  });
}

function setupHomeCarousel() {
  if (!homeCarousel || homeItems.length === 0) {
    return;
  }

  setInterval(() => {
    transitionCarouselItem(
      homeCarouselFrame,
      homeItems,
      currentHomeIndex + 1,
      () => ({ index: currentHomeIndex, isAnimating: isHomeAnimating }),
      (state) => {
        currentHomeIndex = state.index;
        isHomeAnimating = state.isAnimating;
      }
    );
  }, 4500);
}

function setupKeyboardControls() {
  document.addEventListener("keydown", (event) => {
    if (lightbox?.open) {
      if (event.key === "ArrowRight") {
        showLightboxImage(currentLightboxIndex + 1);
      }

      if (event.key === "ArrowLeft") {
        showLightboxImage(currentLightboxIndex - 1);
      }

      return;
    }

    const sequencePanel = document.querySelector('[data-panel="sequence"]');
    const sequenceIsActive = sequencePanel?.classList.contains("is-active");

    if (!sequenceIsActive || selectedWorksItems.length === 0) {
      return;
    }

    if (event.key === "ArrowRight") {
      showSequenceItem(currentSequenceIndex + 1);
    }

    if (event.key === "ArrowLeft") {
      showSequenceItem(currentSequenceIndex - 1);
    }
  });
}

function showLightboxImage(index) {
  if (isLightboxAnimating || thumbnailButtons.length === 0) {
    return;
  }

  isLightboxAnimating = true;
  currentLightboxIndex = (index + thumbnailButtons.length) % thumbnailButtons.length;
  const image = thumbnailButtons[currentLightboxIndex].querySelector("img");
  const currentImage = lightboxFrame.querySelector(".lightbox-image");
  const preloadedImage = new Image();

  preloadedImage.src = getLightboxSource(image);
  preloadedImage.alt = image.alt.replace("Open ", "");
  preloadedImage.draggable = false;

  preloadedImage.addEventListener(
    "load",
    () => {
      currentImage.src = preloadedImage.src;
      currentImage.alt = preloadedImage.alt;
      updateLightboxOrientation(currentImage);

      if (lightboxCount) {
        lightboxCount.textContent = `${currentLightboxIndex + 1}/${thumbnailButtons.length}`;
      }

      lightboxFrame.classList.add("is-loaded");
      preloadAdjacentLightboxImages();

      currentImage.classList.remove("is-transitioning");

      requestAnimationFrame(() => {
        currentImage.classList.add("is-transitioning");
      });

      currentImage.addEventListener(
        "animationend",
        () => {
          currentImage.classList.remove("is-transitioning");
          isLightboxAnimating = false;
        },
        { once: true }
      );
    },
    { once: true }
  );
}

function showSequenceItem(index) {
  transitionCarouselItem(
    projectCarouselFrame,
    selectedWorksItems,
    index,
    () => ({ index: currentSequenceIndex, isAnimating }),
    (state) => {
      currentSequenceIndex = state.index;
      isAnimating = state.isAnimating;

      if (carouselCount) {
        carouselCount.textContent = `${currentSequenceIndex + 1}/${selectedWorksItems.length}`;
      }
    }
  );
}

function transitionCarouselItem(frame, items, index, getState, setState) {
  if (!frame || items.length === 0) {
    return;
  }

  const state = getState();

  if (state.isAnimating) {
    return;
  }

  const nextIndex = (index + items.length) % items.length;
  const item = items[nextIndex];
  const currentImage = frame.querySelector(".carousel-image");
  const nextImage = document.createElement("img");

  setState({ index: nextIndex, isAnimating: true });

  nextImage.className = "carousel-image is-entering";
  nextImage.src = item.src || item.main;
  nextImage.alt = item.alt;

  nextImage.addEventListener(
    "load",
    () => {
      frame.append(nextImage);

      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          currentImage.classList.add("is-leaving");
          nextImage.classList.add("is-active");
        });
      });

      nextImage.addEventListener(
        "animationend",
        () => {
          currentImage.remove();
          nextImage.className = "carousel-image is-active";
          setState({ index: nextIndex, isAnimating: false });
        },
        { once: true }
      );
    },
    { once: true }
  );
}

function getLightboxSource(image) {
  return image.dataset.full || image.src;
}

function preloadAdjacentLightboxImages() {
  if (thumbnailButtons.length === 0) {
    return;
  }

  const previousIndex = (currentLightboxIndex - 1 + thumbnailButtons.length) % thumbnailButtons.length;
  const nextIndex = (currentLightboxIndex + 1) % thumbnailButtons.length;

  [previousIndex, nextIndex].forEach((index) => {
    const image = thumbnailButtons[index].querySelector("img");
    preloadImage(getLightboxSource(image));
  });
}

function preloadImage(src) {
  if (!src || preloadedImages.has(src)) {
    return;
  }

  preloadedImages.add(src);
  const image = new Image();
  image.src = src;
}

function revealLightboxFrameWhenReady(image) {
  const reveal = () => {
    lightboxFrame.classList.add("is-loaded");
  };

  if (image.complete) {
    reveal();
    return;
  }

  image.addEventListener("load", reveal, { once: true });
}

function updateLightboxOrientation(image) {
  const applyOrientation = () => {
    const isPortrait = image.naturalHeight > image.naturalWidth;

    lightboxFrame.classList.toggle("is-portrait", isPortrait);
    lightboxFrame.classList.toggle("is-landscape", !isPortrait);
  };

  if (image.complete) {
    applyOrientation();
    return;
  }

  image.addEventListener("load", applyOrientation, { once: true });
}
