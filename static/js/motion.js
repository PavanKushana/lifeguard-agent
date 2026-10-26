/*
  motion.js
  The movement on the page:
  - smooth scrolling with Lenis (on computers with a mouse or trackpad; phones keep their own),
  - the first screen rising in, piece by piece,
  - sections fading in as they scroll into view,
  - the "How it works" line drawing itself, each step lighting up when the line reaches it,
  - the numbers counting up once,
  - a soft light following the mouse over cards,
  - the header bar turning to solid glass once the page scrolls.
  GSAP does the animations; its ScrollTrigger plugin starts them at the right scroll position.

  Only decoration: if this file or the libraries don't load, the page and the demo work as before
  (style.css shows everything after 2.5 s anyway). Visitors who asked their computer for less motion
  get none of it.
*/

(function () {
  const root = document.documentElement;
  const lessMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const bar = document.getElementById("top-bar");

  // The header bar: see-through at the top, solid glass once the page scrolls.
  const onScroll = () => { if (bar) bar.classList.toggle("scrolled", window.scrollY > 8); };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // The light on cards (.spotlight) sits where the mouse is.
  document.addEventListener("pointermove", (event) => {
    const card = event.target.closest ? event.target.closest(".spotlight") : null;
    if (!card) return;
    const box = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${event.clientX - box.left}px`);
    card.style.setProperty("--my", `${event.clientY - box.top}px`);
  }, { passive: true });

  if (lessMotion || !window.gsap) {
    root.classList.remove("motion");  // show everything at once, nothing fades
    return;
  }

  const gsap = window.gsap;
  const ScrollTrigger = window.ScrollTrigger;
  if (ScrollTrigger) gsap.registerPlugin(ScrollTrigger);
  root.classList.add("motion-ready");  // from here GSAP shows the hidden things, not the 2.5 s fallback

  // Smooth scrolling. Boxes that scroll on their own (the agent's long outputs) keep working:
  // allowNestedScroll lets the wheel scroll them instead of the page.
  if (window.Lenis && matchMedia("(pointer: fine)").matches) {
    const lenis = new window.Lenis({ duration: 1.1, smoothWheel: true, allowNestedScroll: true });
    window.lenis = lenis;  // app.js (changing steps) and hero3d.js (click on the 3D shop) scroll through it
    if (ScrollTrigger) lenis.on("scroll", ScrollTrigger.update);
    gsap.ticker.add((time) => lenis.raf(time * 1000));
    gsap.ticker.lagSmoothing(0);
  }

  // Links to a part of the page (#demo, #how...) glide there and stop just under the header bar.
  document.addEventListener("click", (event) => {
    const link = event.target.closest ? event.target.closest('a[href^="#"]') : null;
    if (!link) return;
    const id = link.getAttribute("href");
    const target = id === "#top" ? 0 : document.querySelector(id);
    if (target === null) return;
    event.preventDefault();
    const offset = -(bar ? bar.offsetHeight : 64);
    if (window.lenis) {
      window.lenis.scrollTo(target, { offset });
    } else {
      const top = target === 0 ? 0 : target.getBoundingClientRect().top + window.scrollY + offset;
      window.scrollTo({ top, behavior: "smooth" });
    }
    history.replaceState(null, "", id);
  });

  // The first screen: the small label, headline, text, buttons, tags and the 3D picture rise in one by one.
  gsap.fromTo("[data-hero]", { opacity: 0, y: 26, filter: "blur(6px)" }, {
    opacity: 1, y: 0, filter: "blur(0px)", duration: 0.9, ease: "power3.out", stagger: 0.09, delay: 0.05,
    clearProps: "filter,transform",
  });

  if (!ScrollTrigger) {
    gsap.set("[data-reveal]", { opacity: 1 });
    return;
  }

  // Everything marked data-reveal fades up when it scrolls into view, a few at a time.
  ScrollTrigger.batch("[data-reveal]", {
    start: "top 88%",
    once: true,
    onEnter: (items) => gsap.fromTo(items, { opacity: 0, y: 28 }, {
      opacity: 1, y: 0, duration: 0.8, ease: "power3.out", stagger: 0.08, clearProps: "transform",
    }),
  });

  // How it works: the steps rise in, the line draws itself, and each step lights up as the line reaches it.
  const flow = document.getElementById("flow");
  if (flow) {
    const steps = [...flow.querySelectorAll(".flow-step")];
    gsap.set(steps, { opacity: 0, y: 24 });
    flow.style.setProperty("--flow-progress", "0");
    ScrollTrigger.create({
      trigger: flow,
      start: "top 80%",
      once: true,
      onEnter: () => {
        const timeline = gsap.timeline();
        timeline.to(steps, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out", stagger: 0.12, clearProps: "transform" });
        timeline.to(flow, { "--flow-progress": 1, duration: 1.3, ease: "power2.inOut" }, 0.15);
        steps.forEach((step, index) => timeline.call(() => step.classList.add("lit"), null, 0.35 + index * 0.19));
      },
    });
  }

  // The numbers count up once, from 0 to their value.
  for (const number of document.querySelectorAll("[data-count]")) {
    const end = Number(number.dataset.count);
    const counter = { value: 0 };
    number.textContent = "0";
    ScrollTrigger.create({
      trigger: number,
      start: "top 92%",
      once: true,
      onEnter: () => gsap.to(counter, {
        value: end, duration: 1.2, ease: "power2.out",
        onUpdate: () => { number.textContent = String(Math.round(counter.value)); },
      }),
    });
  }
})();
