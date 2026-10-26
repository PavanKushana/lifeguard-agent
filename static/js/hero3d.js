/*
  hero3d.js
  The live 3D picture of the demo shop at the top of the page, drawn with three.js (WebGL).

  It shows the same shop as the demo below: customers -> web -> api -> database and cache, each
  container as a small stack of server units (like the logo), with requests flowing between them as
  little lights. It is live: app.js tells it how each part is doing (publishStatus), so a broken part
  turns red and the requests stop on their way to it, and the glowing orb (the agent) flies to the part
  the agent is checking right now and scans it.

  Kept light on purpose, so it runs on phones and the free server: simple shapes, no shadows or
  post-processing, drawing only while the picture is on screen, a lower resolution on phones, and one
  still frame (redrawn on changes) for visitors who asked their computer for less motion.
*/

import * as THREE from "three";
import { CSS2DObject, CSS2DRenderer } from "/static/vendor/three/addons/renderers/CSS2DRenderer.js";

// Where each part stands on the platform (x: left to right along the request's path, z: front/back).
const PARTS = {
  customers: { x: -3.9, z: 0.3 },
  web: { x: -1.55, z: 0.3 },
  api: { x: 0.8, z: 0.3 },
  db: { x: 3.15, z: -1.15 },
  cache: { x: 3.15, z: 1.75 },
};
// The platform under everything: its size and where its middle is.
const PLATFORM = { width: 10.2, depth: 6.0, x: -0.3, z: 0.3 };
// The paths a request takes. Each line gets the colour of the part it leads to, like the 2D map.
const LINKS = [["customers", "web"], ["web", "api"], ["api", "db"], ["api", "cache"]];
const SERVERS = ["web", "api", "db", "cache"];
const PACKETS_PER_LINK = 4;
const UNIT_HEIGHT = 0.3;    // one server unit in a stack
const UNIT_GAP = 0.065;
const UNIT_SIZE = 1.42;     // width and depth of a server unit

const visual = document.getElementById("hero-visual");
const canvas = document.getElementById("hero-canvas");
const statusPill = document.getElementById("hero-status");
const statusText = document.getElementById("hero-status-text");
const lessMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const phone = matchMedia("(max-width: 640px)");

if (visual && canvas) {
  try {
    start();
  } catch (error) {
    // No WebGL (very old device or switched off): style.css hides the empty stage, the page still works.
    console.warn("3D picture off:", error);
    document.documentElement.classList.add("no-webgl");
  }
}

/** Build the scene, connect it to the demo's live state, and start drawing. */
function start() {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "default" });
  renderer.setClearColor(0x000000, 0);  // see-through: the page's background shows behind the scene

  // The names over the parts are HTML (sharp text, styled by style.css), placed by three.js's CSS2DRenderer.
  const labels = new CSS2DRenderer();
  labels.domElement.className = "hero-labels";
  visual.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
  const target = new THREE.Vector3(-0.25, 0.25, 0.3);  // the point the camera looks at

  let colours = readColours();
  const world = buildWorld(scene, colours);
  let status = { parts: null, focus: null };
  const pointer = { x: 0, y: 0, smoothX: 0, smoothY: 0 };
  let hovered = null;
  let fitDistance = 20;   // how far back the camera must stand to see everything (set by fitCamera)

  // ---------- Camera ----------

  const AZIMUTH = 0.74;     // the camera looks from the front-right...
  const ELEVATION = 0.8;    // ...and from above, steep enough that the platform fills the box's height
  /** Put the camera at an angle and distance around the point it looks at. */
  const pose = (azimuth, elevation, distance) => {
    camera.position.set(
      target.x + distance * Math.cos(elevation) * Math.sin(azimuth),
      target.y + distance * Math.sin(elevation),
      target.z + distance * Math.cos(elevation) * Math.cos(azimuth),
    );
    camera.lookAt(target);
    camera.updateMatrixWorld(true);
  };

  // The points that must stay in the picture: the platform's outline (top and bottom edge),
  // and the name tags over the parts.
  const corners = [];
  for (const point of roundedRect(PLATFORM.width + 0.12, PLATFORM.depth + 0.12, 0.9).getPoints(6)) {
    for (const y of [-0.36, 0]) corners.push(new THREE.Vector3(PLATFORM.x + point.x, y, PLATFORM.z - point.y));
  }
  for (const { x, z } of Object.values(PARTS)) corners.push(new THREE.Vector3(x, 2.25, z));
  const FILL = 0.88;  // the scene fills 88% of the box...
  const LIFT = 0.07;  // ...and sits a little above the middle, leaving room for the status pill at the bottom
  const startTarget = target.clone();
  /**
   * Aim and step back so the whole scene fills the box, whatever its shape (wide laptop box, phone box).
   * Measured, not guessed: project the points onto the screen, move the aim to the middle of what the
   * camera sees, step back or forward until it fits, repeat. (Looking down from above, the platform
   * would otherwise sit low in the box with empty space on top.)
   */
  const fitCamera = () => {
    let distance = 20;
    target.copy(startTarget);
    for (let round = 0; round < 6; round++) {
      pose(AZIMUTH, ELEVATION, distance);
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const corner of corners) {
        const point = corner.clone().project(camera);
        minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
        minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
      }
      const halfHeight = distance * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
      target.addScaledVector(right, ((minX + maxX) / 2) * halfHeight * camera.aspect);
      target.addScaledVector(up, ((minY + maxY) / 2 - LIFT) * halfHeight);
      distance *= Math.max((maxX - minX) / 2, (maxY - minY) / 2) / FILL;
    }
    fitDistance = distance;
  };

  // ---------- Drawing ----------

  /** Where the camera stands this frame: leaning a little with the mouse, the time and the scroll. */
  const placeCamera = (time) => {
    pointer.smoothX += (pointer.x - pointer.smoothX) * 0.05;
    pointer.smoothY += (pointer.y - pointer.smoothY) * 0.05;
    const scrolled = Math.min(1, window.scrollY / Math.max(1, visual.offsetHeight));
    const azimuth = AZIMUTH + pointer.smoothX * 0.12 + (lessMotion ? 0 : Math.sin(time * 0.12) * 0.04);
    const elevation = ELEVATION - pointer.smoothY * 0.05 + scrolled * 0.1;
    pose(azimuth, elevation, fitDistance * (1 + scrolled * 0.06));
  };

  let last = performance.now();
  /** One frame: move the camera and everything in the scene, then draw the picture and the name tags. */
  const draw = (now) => {
    const time = now / 1000;
    const delta = Math.min(0.05, (now - last) / 1000 || 0);
    last = now;
    placeCamera(time);
    animate(world, status, colours, time, delta, hovered, lessMotion);
    renderer.render(scene, camera);
    labels.render(scene, camera);
  };

  // ---------- Live state from the demo ----------

  /** Take the demo's latest state (from app.js) and show it. */
  const applyStatus = (detail, instant = false) => {
    status = { parts: detail?.parts || status.parts, focus: detail ? detail.focus : status.focus };
    updateParts(world, status, colours, instant);
    updatePill(status.parts);
    if (lessMotion) draw(0);
  };
  window.addEventListener("lifeguard:status", (event) => applyStatus(event.detail));

  // The sun/moon button: re-read the colours and repaint.
  new MutationObserver(() => {
    colours = readColours();
    paintTheme(world, colours);
    updateParts(world, status, colours, true);
    if (lessMotion) draw(0);
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // ---------- Size and input ----------

  /** Fit the renderer to its box on the page (the camera's distance follows the box's shape). */
  const resize = () => {
    const { width, height } = visual.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, phone.matches ? 1.5 : 2));
    renderer.setSize(width, height, false);
    labels.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    fitCamera();
    if (lessMotion) draw(0);
  };
  new ResizeObserver(resize).observe(visual);
  resize();
  // app.js may have published the shop's state before this file loaded.
  applyStatus(window.lifeguardStatus || { parts: null, focus: null }, true);

  // The scene leans a little towards the mouse, which makes it feel like a real object.
  window.addEventListener("pointermove", (event) => {
    pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
    pointer.y = (event.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  // Hovering a server lifts it a little; a click jumps to the demo, where it can be broken.
  const raycaster = new THREE.Raycaster();
  canvas.addEventListener("pointermove", (event) => {
    const box = canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - box.left) / box.width) * 2 - 1, -((event.clientY - box.top) / box.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(world.pickables, false)[0];
    hovered = hit ? hit.object.userData.part : null;
    canvas.style.cursor = hovered ? "pointer" : "";
  });
  canvas.addEventListener("pointerleave", () => { hovered = null; canvas.style.cursor = ""; });
  canvas.addEventListener("click", () => {
    if (!hovered) return;
    const demo = document.getElementById("demo");
    if (window.lenis) window.lenis.scrollTo(demo, { offset: -10 });
    else demo.scrollIntoView({ behavior: "smooth" });
  });

  if (lessMotion) {
    draw(0);  // one still frame; applyStatus and resize draw again when something changes
    return;
  }

  // Draw only while the picture is on screen and the tab is visible: no wasted battery further down the page.
  let onScreen = true;
  new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; }, { threshold: 0 }).observe(visual);
  // On a phone, every second frame is enough (about 30 a second): it saves battery, and the movement still
  // looks the same because everything moves by the clock, not by the number of frames.
  let frame = 0;
  const loop = (now) => {
    if (onScreen && !document.hidden && (!phone.matches || frame++ % 2 === 0)) draw(now);
    else if (!onScreen || document.hidden) last = now;
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

/** The colours from style.css (--ok, --bad, --scene-block...), so the 3D picture follows the theme. */
function readColours() {
  const css = getComputedStyle(document.documentElement);
  const get = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    ok: new THREE.Color(get("--ok", "#5df0a8")),
    warn: new THREE.Color(get("--warn", "#fbbf24")),
    bad: new THREE.Color(get("--bad", "#ff6b6b")),
    agent: new THREE.Color(get("--agent", "#6cb6ff")),
    accent: new THREE.Color(get("--accent", "#5df0a8")),
    block: new THREE.Color(get("--scene-block", "#172142")),
    platform: new THREE.Color(get("--scene-platform", "#0d1430")),
    edge: new THREE.Color(get("--scene-edge", "#3a4f8a")),
    person: new THREE.Color(get("--scene-person", "#8d97b5")),
    dark: document.documentElement.dataset.theme !== "light",
  };
}

// ---------- Building the scene ----------

/** A rectangle with round corners, as a flat shape (extruded into the platform and the server units). */
function roundedRect(width, depth, radius) {
  const shape = new THREE.Shape();
  const x = -width / 2;
  const y = -depth / 2;
  shape.moveTo(x + radius, y);
  shape.lineTo(x + width - radius, y);
  shape.quadraticCurveTo(x + width, y, x + width, y + radius);
  shape.lineTo(x + width, y + depth - radius);
  shape.quadraticCurveTo(x + width, y + depth, x + width - radius, y + depth);
  shape.lineTo(x + radius, y + depth);
  shape.quadraticCurveTo(x, y + depth, x, y + depth - radius);
  shape.lineTo(x, y + radius);
  shape.quadraticCurveTo(x, y, x + radius, y);
  return shape;
}

/** A shape pulled up into a solid block of the given height, standing on y = 0. */
function slab(shape, height, bevel = 0.03) {
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: height, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 2, curveSegments: 10,
  });
  geometry.rotateX(-Math.PI / 2);  // extruded along z by default; stand it up
  return geometry;
}

/** A soft round light (white in the middle, see-through at the edge), for glows and halos. */
function glowTexture() {
  const size = 128;
  const paint = document.createElement("canvas");
  paint.width = paint.height = size;
  const context = paint.getContext("2d");
  const gradient = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, "rgba(255,255,255,1)");
  gradient.addColorStop(0.25, "rgba(255,255,255,0.55)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(paint);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** A light that fades from top to bottom, for the agent's scanning beam. */
function beamTexture() {
  const paint = document.createElement("canvas");
  paint.width = 4;
  paint.height = 128;
  const context = paint.getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 128);
  gradient.addColorStop(0, "rgba(255,255,255,0.7)");
  gradient.addColorStop(1, "rgba(255,255,255,0.03)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 4, 128);
  const texture = new THREE.CanvasTexture(paint);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** A glow sprite: always faces the camera, adds light instead of covering what's behind it. */
function glowSprite(texture, colour, scale) {
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture, color: colour, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
  sprite.scale.setScalar(scale);
  return sprite;
}

/** An HTML name tag that floats over a part ("db" / "disk 100%"). */
function label(name) {
  const element = document.createElement("div");
  element.className = "node-label";
  element.innerHTML = `<b>${name}</b><span>OK</span><em>agent checking</em>`;
  return new CSS2DObject(element);
}

/** Everything in the scene: platform, servers, customers, request lines, the agent and the floating dust. */
function buildWorld(scene, colours) {
  const glow = glowTexture();
  const world = { parts: {}, links: [], pickables: [], glow };

  // Light: a soft sky/ground light, a sun from the front-right, and two coloured side lights for depth.
  world.hemi = new THREE.HemisphereLight(0xdfe9ff, 0x0b1020, 1.4);
  world.sun = new THREE.DirectionalLight(0xffffff, 2.2);
  world.sun.position.set(6, 12, 8);
  world.mint = new THREE.PointLight(colours.accent, 30, 16, 2);
  world.mint.position.set(-5, 3, 3);
  world.blue = new THREE.PointLight(0x6c8cff, 26, 16, 2);
  world.blue.position.set(6, 3.5, -3);
  scene.add(world.hemi, world.sun, world.mint, world.blue);

  // The platform everything stands on, with a thin lit edge and a soft glow underneath.
  const platformShape = roundedRect(PLATFORM.width, PLATFORM.depth, 0.9);
  world.platform = new THREE.Mesh(slab(platformShape, 0.22, 0.06), new THREE.MeshStandardMaterial({ roughness: 0.6, metalness: 0.15 }));
  world.platform.position.set(PLATFORM.x, -0.3, PLATFORM.z);
  const edgePoints = platformShape.getPoints(24).map((p) => new THREE.Vector3(p.x, 0, -p.y));
  world.edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(edgePoints),
    new THREE.LineBasicMaterial({ transparent: true, opacity: 0.7 }));
  world.edge.position.set(PLATFORM.x, -0.01, PLATFORM.z);
  world.floorGlow = new THREE.Mesh(new THREE.PlaneGeometry(PLATFORM.width * 1.4, PLATFORM.depth * 1.6), new THREE.MeshBasicMaterial({
    map: glow, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.35,
  }));
  world.floorGlow.rotation.x = -Math.PI / 2;
  world.floorGlow.position.set(PLATFORM.x, -0.36, PLATFORM.z);
  scene.add(world.platform, world.edge, world.floorGlow);

  // The four containers: three stacked server units each, a status light on every unit, a lit top edge.
  const unitShape = roundedRect(UNIT_SIZE, UNIT_SIZE, 0.22);
  const unitGeometry = slab(unitShape, UNIT_HEIGHT);
  const ledGeometry = new THREE.SphereGeometry(0.055, 12, 8);
  const side = UNIT_SIZE / 2 + 0.035;  // where a status light sits on a unit's side
  const topPoints = unitShape.getPoints(10).map((p) => new THREE.Vector3(p.x, 0, -p.y));
  for (const name of SERVERS) {
    const { x, z } = PARTS[name];
    const group = new THREE.Group();
    group.position.set(x, 0, z);
    const body = new THREE.MeshStandardMaterial({ roughness: 0.38, metalness: 0.3 });
    const led = new THREE.MeshBasicMaterial();
    const units = [];
    for (let i = 0; i < 3; i++) {
      const unit = new THREE.Mesh(unitGeometry, body);
      unit.position.y = i * (UNIT_HEIGHT + UNIT_GAP);
      unit.userData.part = name;
      world.pickables.push(unit);
      group.add(unit);
      units.push(unit);
      // Two lights per unit, on the two sides the camera sees, so the status reads from any angle.
      for (const [lx, lz] of [[-UNIT_SIZE * 0.3, side], [side, UNIT_SIZE * 0.3]]) {
        const light = new THREE.Mesh(ledGeometry, led);
        light.position.set(lx, unit.position.y + UNIT_HEIGHT / 2, lz);
        group.add(light);
      }
    }
    const topY = 3 * UNIT_HEIGHT + 2 * UNIT_GAP + 0.04;
    const rim = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(topPoints), new THREE.LineBasicMaterial({ transparent: true }));
    rim.position.y = topY;
    // A halo over a broken part: hidden while it's healthy.
    const halo = glowSprite(glow, colours.bad, 2.2);
    halo.position.y = topY + 0.5;
    halo.visible = false;
    const tag = label(name);
    tag.position.set(0, topY + 0.85, 0);
    group.add(rim, halo, tag);
    scene.add(group);
    world.parts[name] = { group, units, body, led, rim, halo, tag, topY, level: "ok", lift: 0,
                          ledColour: colours.ok.clone() };
  }

  // The customers: five small people on a round pad. They wobble and turn red while they complain.
  const customers = new THREE.Group();
  customers.position.set(PARTS.customers.x, 0, PARTS.customers.z);
  const pad = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 1.0, 0.08, 40), new THREE.MeshStandardMaterial({ roughness: 0.5, metalness: 0.2 }));
  pad.position.y = 0.04;
  customers.add(pad);
  const personMaterial = new THREE.MeshStandardMaterial({ roughness: 0.5, metalness: 0.1 });
  const bodyGeometry = new THREE.CapsuleGeometry(0.11, 0.2, 4, 10);
  const headGeometry = new THREE.SphereGeometry(0.09, 16, 12);
  const people = [];
  [[0, 0], [0.42, 0.2], [-0.4, 0.26], [0.18, -0.42], [-0.26, -0.36]].forEach(([px, pz], index) => {
    const person = new THREE.Group();
    const torso = new THREE.Mesh(bodyGeometry, personMaterial);
    torso.position.y = 0.32;
    const head = new THREE.Mesh(headGeometry, personMaterial);
    head.position.y = 0.62;
    person.add(torso, head);
    person.position.set(px, 0.08, pz);
    person.userData.phase = index * 1.3;
    customers.add(person);
    people.push(person);
  });
  const customersTag = label("customers");
  customersTag.position.set(-0.8, 1.2, 0.3);  // a bit to the left, so it doesn't sit on the web server's tag
  customers.add(customersTag);
  scene.add(customers);
  world.parts.customers = { group: customers, pad, people, personMaterial, tag: customersTag, topY: 0.7, level: "ok" };

  // The request lines: a gentle arc from one part to the next, with small lights travelling along it.
  const packetGeometry = new THREE.SphereGeometry(0.06, 12, 8);
  for (const [from, to] of LINKS) {
    const start = new THREE.Vector3(PARTS[from].x, from === "customers" ? 0.45 : 0.55, PARTS[from].z);
    const end = new THREE.Vector3(PARTS[to].x, 0.55, PARTS[to].z);
    const middle = start.clone().lerp(end, 0.5).add(new THREE.Vector3(0, 0.75, 0));
    const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.02, 6, false),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.4, depthWrite: false }));
    const packetMaterial = new THREE.MeshBasicMaterial({ transparent: true });
    const packets = [];
    for (let i = 0; i < PACKETS_PER_LINK; i++) {
      const packet = new THREE.Mesh(packetGeometry, packetMaterial);
      const halo = glowSprite(glow, colours.ok, 0.42);
      packet.add(halo);
      packets.push({ mesh: packet, halo, offset: i / PACKETS_PER_LINK });
      scene.add(packet);
    }
    scene.add(tube);
    world.links.push({ from, to, curve, tube, packetMaterial, packets, colour: colours.ok.clone(), level: "ok" });
  }

  // The agent: a glowing orb with a ring around it. It patrols, and flies to the part being checked.
  const agent = new THREE.Group();
  const orb = new THREE.Mesh(new THREE.SphereGeometry(0.17, 24, 16), new THREE.MeshBasicMaterial());
  const orbGlow = glowSprite(glow, colours.agent, 1.5);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.018, 8, 64), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 }));
  agent.add(orb, orbGlow, ring);
  agent.position.set(-1, 2.4, 0.3);
  // Its scanning beam: a cone of light down onto the part, plus a ring that ripples outward at its base.
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.78, 1, 32, 1, true), new THREE.MeshBasicMaterial({
    map: beamTexture(), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  }));
  beam.visible = false;
  const scan = new THREE.Mesh(new THREE.RingGeometry(0.62, 0.7, 48), new THREE.MeshBasicMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
  }));
  scan.rotation.x = -Math.PI / 2;
  scan.visible = false;
  scene.add(agent, beam, scan);
  world.agent = { group: agent, orb, orbGlow, ring, beam, scan, focusFor: 0 };

  // Floating dust: a few faint specks drifting upward, for depth.
  const dustCount = 140;
  const positions = new Float32Array(dustCount * 3);
  for (let i = 0; i < dustCount; i++) {
    positions[i * 3] = (Math.random() - 0.5) * 15;
    positions[i * 3 + 1] = Math.random() * 5;
    positions[i * 3 + 2] = (Math.random() - 0.5) * 9;
  }
  const dustGeometry = new THREE.BufferGeometry();
  dustGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  world.dust = new THREE.Points(dustGeometry, new THREE.PointsMaterial({
    size: 0.05, transparent: true, opacity: 0.5, depthWrite: false, sizeAttenuation: true,
  }));
  scene.add(world.dust);

  paintTheme(world, colours);
  return world;
}

/** Colours that depend on the theme (dark or light): platform, servers, people, lights, dust. */
function paintTheme(world, colours) {
  world.platform.material.color.copy(colours.platform);
  world.edge.material.color.copy(colours.edge);
  world.floorGlow.material.color.copy(colours.accent);
  world.floorGlow.material.opacity = colours.dark ? 0.32 : 0.22;
  world.mint.color.copy(colours.accent);
  world.mint.intensity = colours.dark ? 30 : 10;
  world.blue.intensity = colours.dark ? 26 : 8;
  world.hemi.intensity = colours.dark ? 1.4 : 2.6;
  world.hemi.groundColor.set(colours.dark ? 0x0b1020 : 0xc9d3e6);
  for (const name of SERVERS) world.parts[name].body.color.copy(colours.block);
  const customers = world.parts.customers;
  customers.pad.material.color.copy(colours.block);
  customers.personMaterial.color.copy(colours.person);
  world.dust.material.color.copy(colours.dark ? colours.accent : colours.edge);
  world.agent.orb.material.color.copy(colours.agent);
  world.agent.orbGlow.material.color.copy(colours.agent);
  world.agent.ring.material.color.copy(colours.agent);
  world.agent.beam.material.color.copy(colours.agent);
  world.agent.scan.material.color.copy(colours.agent);
}

// ---------- Following the demo's state ----------

/** The colour for a level: ok (mint), warn (amber) or bad (red). */
function levelColour(level, colours) {
  return level === "bad" ? colours.bad : level === "warn" ? colours.warn : colours.ok;
}

/** Apply the demo's latest state: colours, name tags, which lines are blocked, where the agent goes. */
function updateParts(world, status, colours, instant) {
  const parts = status.parts || {};
  for (const name of Object.keys(PARTS)) {
    const part = world.parts[name];
    const info = parts[name] || { level: "ok", notes: [] };
    part.level = info.level;
    // Short notes: the tags are small ("main disk 90%" -> "disk 90%"; the 2D map below keeps the full words).
    const note = (info.notes[0] || (name === "customers" ? "happy" : "OK")).replace("main disk", "disk");
    const element = part.tag.element;
    element.className = `node-label ${info.level}${status.focus === name ? " focus" : ""}`;
    element.querySelector("span").textContent = note;  // "agent checking" is its own line, shown by .focus
    if (part.halo) {
      part.halo.visible = info.level !== "ok";
      part.halo.material.color.copy(levelColour(info.level, colours));
    }
    if (part.ledColour) {
      part.ledColour.copy(levelColour(info.level, colours));
      if (instant) {
        part.led.color.copy(part.ledColour);
        part.rim.material.color.copy(part.ledColour);
      }
    }
  }
  for (const link of world.links) {
    link.level = world.parts[link.to].level;
    link.colour.copy(levelColour(link.level, colours));
    if (instant) {
      link.tube.material.color.copy(link.colour);
      link.packetMaterial.color.copy(link.colour);
    }
    for (const packet of link.packets) packet.halo.material.color.copy(link.colour);
  }
  world.focus = status.focus && PARTS[status.focus] ? status.focus : null;
}

/** The small status pill on the 3D picture: "All systems normal", or what's wrong right now. */
function updatePill(parts) {
  if (!statusPill || !statusText) return;
  let level = "ok";
  let text = "All systems normal";
  if (parts) {
    // The worst server (it says where it hurts), e.g. "db: disk 100%".
    const order = { bad: 2, warn: 1, ok: 0 };
    const worst = SERVERS.filter((name) => parts[name] && parts[name].level !== "ok")
      .sort((a, b) => order[parts[b].level] - order[parts[a].level])[0];
    const problem = worst ? `${worst}: ${parts[worst].notes[0] || "problem"}` : "";
    const fixed = parts.customers && parts.customers.notes[0] === "happy again";
    if (fixed) {
      // After a fix that worked, "Fixed" comes first; a leftover warning (e.g. a fuller disk) is something to watch.
      level = worst ? parts[worst].level : "ok";
      text = worst ? `Fixed · watch ${problem}` : "Fixed. Customers are happy again";
    } else if (worst) {
      level = parts[worst].level;
      text = problem;
    } else if (parts.customers && parts.customers.level === "bad") {
      level = "bad";
      text = "Customers are complaining";
    }
  }
  statusPill.dataset.level = level;
  statusText.textContent = text;
}

// ---------- Every frame ----------

/** Move everything a little: packets along their lines, the agent, the people, the colour fades. */
function animate(world, status, colours, time, delta, hovered, still) {
  const fade = still ? 1 : 1 - Math.exp(-delta * 7);  // how far colours move towards their new value this frame

  // Servers: colours fade to the new state; a hovered one lifts; a broken one's halo pulses.
  for (const name of SERVERS) {
    const part = world.parts[name];
    part.led.color.lerp(part.ledColour, fade);
    part.rim.material.color.lerp(part.ledColour, fade);
    const blink = part.level === "bad" && !still ? 0.55 + 0.45 * Math.sin(time * 6) : 1;
    part.rim.material.opacity = 0.9 * blink;
    part.lift += ((hovered === name ? 0.12 : 0) - part.lift) * (still ? 1 : 0.15);
    part.group.position.y = part.lift;
    if (part.halo.visible) {
      const pulse = still ? 0.5 : 0.5 + 0.5 * Math.sin(time * 3.2);
      part.halo.scale.setScalar(1.7 + pulse * 0.7);
      part.halo.material.opacity = part.level === "bad" ? 0.55 + pulse * 0.35 : 0.35;
    }
  }

  // Customers: calm and still when happy, fidgeting and red-tinged while they complain.
  const customers = world.parts.customers;
  const upset = customers.level === "bad";
  customers.personMaterial.color.lerp(upset ? colours.bad.clone().lerp(colours.person, 0.35) : colours.person, fade);
  customers.people.forEach((person) => {
    const bounce = upset && !still ? Math.abs(Math.sin(time * 5 + person.userData.phase)) * 0.12 : 0;
    person.position.y = 0.08 + bounce;
  });

  // Request lines: lights travel from one part to the next. Into a broken part they get halfway, then fade out.
  for (const link of world.links) {
    link.tube.material.color.lerp(link.colour, fade);
    link.packetMaterial.color.lerp(link.colour, fade);
    link.tube.material.opacity = link.level === "bad" ? 0.55 : 0.38;
    const speed = link.level === "ok" ? 0.32 : link.level === "warn" ? 0.18 : 0.26;
    for (const packet of link.packets) {
      const t = still ? packet.offset : (time * speed + packet.offset) % 1;
      const reach = link.level === "bad" ? Math.min(t, 0.55) : t;
      packet.mesh.position.copy(link.curve.getPointAt(reach));
      // Fade in at the start, out at the end; a blocked request fades as it gets stuck.
      let size = Math.min(1, t * 6, (1 - t) * 6);
      if (link.level === "bad") size = t < 0.55 ? Math.min(1, t * 6) : Math.max(0, 1 - (t - 0.55) * 5);
      packet.mesh.scale.setScalar(Math.max(0.001, size));
    }
  }

  // The agent: patrols over the shop, or hovers over the part it's checking and scans it.
  const agent = world.agent;
  const goal = new THREE.Vector3();
  if (world.focus) {
    const part = world.parts[world.focus];
    goal.set(PARTS[world.focus].x, part.topY + 1.75 + (still ? 0 : Math.sin(time * 2) * 0.08), PARTS[world.focus].z);
    agent.focusFor += delta;
  } else {
    goal.set(-1.2 + Math.sin(time * 0.23) * 3.2, 2.5 + Math.sin(time * 1.1) * 0.12, 0.35 + Math.sin(time * 0.41) * 1.1);
    agent.focusFor = 0;
  }
  agent.group.position.lerp(goal, still ? 1 : 1 - Math.exp(-delta * 2.2));
  agent.ring.rotation.x = Math.PI / 2 + Math.sin(time * 0.9) * 0.35;
  agent.ring.rotation.y = time * 1.4;
  agent.orbGlow.scale.setScalar(1.35 + (still ? 0 : Math.sin(time * 3) * 0.15));

  const scanning = Boolean(world.focus) && (still || agent.group.position.distanceTo(goal) < 0.6);
  agent.beam.visible = scanning;
  agent.scan.visible = scanning;
  if (scanning) {
    const part = world.parts[world.focus];
    const top = part.topY + 0.02;
    const height = Math.max(0.2, agent.group.position.y - top - 0.1);
    agent.beam.scale.set(1, height, 1);
    agent.beam.position.set(agent.group.position.x, top + height / 2, agent.group.position.z);
    agent.beam.material.opacity = still ? 0.7 : 0.55 + Math.sin(time * 5) * 0.2;
    const ripple = still ? 0.4 : (time * 0.9) % 1;
    agent.scan.position.set(PARTS[world.focus].x, top, PARTS[world.focus].z);
    agent.scan.scale.setScalar(0.7 + ripple * 0.9);
    agent.scan.material.opacity = 0.9 * (1 - ripple);
  }

  // Dust drifts up and starts again at the bottom.
  if (!still) {
    const positions = world.dust.geometry.attributes.position;
    for (let i = 0; i < positions.count; i++) {
      let y = positions.getY(i) + delta * 0.12;
      if (y > 5) y = 0;
      positions.setY(i, y);
    }
    positions.needsUpdate = true;
  }
}
