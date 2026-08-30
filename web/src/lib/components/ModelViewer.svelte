<script>
  // OpenPrintHQ - 3D preview for a library model file (STL / 3MF).
  // SPDX-License-Identifier: AGPL-3.0-or-later
  //
  // three is imported dynamically. It is the largest dependency in the app by
  // some margin, and only this page needs it, so loading it with the route
  // rather than the bundle keeps every other page the size it was.
  //
  // G-code is NOT handled here: GcodeViewer already parses and draws it on a
  // plain canvas, and a second implementation of that is the thing most likely
  // to drift.
  let { url, fileType = '', fallbackThumb = '' } = $props();

  let host;                       // the div three renders into
  let status = $state('loading'); // loading | ok | empty | error
  let message = $state('');

  // Everything three owns, so it can all be torn down together. A viewer left
  // running after navigation keeps a WebGL context alive, and browsers cap
  // those: enough of them and the next page renders nothing at all.
  let teardown = null;

  function disposeAll() {
    if (teardown) { teardown(); teardown = null; }
  }

  async function build() {
    disposeAll();
    status = 'loading'; message = '';
    if (!url || !host) return;

    const is3mf = fileType === '3mf' || url.toLowerCase().includes('.3mf');
    let THREE, Loader, OrbitControls;
    try {
      THREE = await import('three');
      OrbitControls = (await import('three/examples/jsm/controls/OrbitControls.js')).OrbitControls;
      Loader = is3mf
        ? (await import('three/examples/jsm/loaders/3MFLoader.js')).ThreeMFLoader
        : (await import('three/examples/jsm/loaders/STLLoader.js')).STLLoader;
    } catch (e) {
      status = 'error'; message = 'could not load the 3D viewer';
      return;
    }
    // The element can go away while those imports are in flight.
    if (!host) return;

    const width = host.clientWidth || 480;
    const height = host.clientHeight || 360;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 5000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    host.replaceChildren(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.0;

    scene.add(new THREE.AmbientLight(0x808098, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(1, 2, 1); scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.7); fill.position.set(-2, -1, -1); scene.add(fill);
    scene.add(new THREE.GridHelper(200, 30, 0x555566, 0x33333f));

    let animId = null;
    let ro = null;
    let disposed = false;

    teardown = () => {
      disposed = true;
      if (animId) cancelAnimationFrame(animId);
      if (ro) ro.disconnect();
      controls.dispose();
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
      });
      renderer.dispose();
      // forceContextLoss frees the GPU context immediately rather than when the
      // renderer happens to be collected, which is what actually keeps the
      // per-page context count from creeping up.
      renderer.forceContextLoss?.();
    };

    const frame = (target) => {
      const box = new THREE.Box3().setFromObject(target);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 1);
      const fov = camera.fov * (Math.PI / 180);
      const dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 2.2;
      camera.position.set(dist * 0.6, dist * 0.5, dist);
      controls.target.copy(center);
      controls.update();
    };

    new Loader().load(
      url,
      (result) => {
        if (disposed) return;
        let target;
        if (is3mf) {
          const box = new THREE.Box3().setFromObject(result);
          const size = box.getSize(new THREE.Vector3());
          // A Bambu Studio 3MF carries no standard geometry, so the loader
          // succeeds and hands back an empty scene. That is not an error and
          // must not be reported as one: show the thumbnail instead.
          if (box.isEmpty() || (size.x === 0 && size.y === 0 && size.z === 0)) {
            status = 'empty';
            disposeAll();
            return;
          }
          const center = box.getCenter(new THREE.Vector3());
          const scale = 60 / Math.max(size.x, size.y, size.z);
          result.scale.setScalar(scale);
          result.position.set(-center.x * scale, -center.y * scale + (size.y * scale) / 2, -center.z * scale);
          target = result;
        } else {
          const geometry = result;
          if (!geometry.attributes.normal || geometry.attributes.normal.count === 0) geometry.computeVertexNormals();
          geometry.center();
          geometry.computeBoundingBox();
          const size = geometry.boundingBox.getSize(new THREE.Vector3());
          const scale = 60 / Math.max(size.x, size.y, size.z, 1);
          const mesh = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
            color: 0x00ccee, specular: 0x333355, shininess: 35
          }));
          mesh.scale.setScalar(scale);
          // STL is Z-up; the scene is Y-up.
          mesh.rotation.x = -Math.PI / 2;
          mesh.position.y = (size.z * scale) / 2;
          target = mesh;
        }
        scene.add(target);
        frame(target);
        status = 'ok';
      },
      undefined,
      () => {
        if (disposed) return;
        status = 'error';
        message = 'could not load the 3D preview';
        disposeAll();
      }
    );

    const animate = () => {
      animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    ro = new ResizeObserver(() => {
      const w = host?.clientWidth, h = host?.clientHeight;
      if (w > 0 && h > 0) {
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      }
    });
    ro.observe(host);
  }

  $effect(() => {
    url; fileType;
    build();
    return disposeAll;
  });
</script>

<div class="wrap">
  <div class="host" bind:this={host} class:hidden={status !== 'ok' && status !== 'loading'}></div>
  {#if status === 'loading'}
    <span class="note">Loading preview...</span>
  {:else if status === 'empty'}
    {#if fallbackThumb}
      <img class="fallback" src={fallbackThumb} alt="" />
    {:else}
      <span class="note">This 3MF carries no geometry to preview.</span>
    {/if}
  {:else if status === 'error'}
    <span class="note">{message}</span>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    width: 100%;
    aspect-ratio: 4 / 3;
    border: 1px solid var(--ophq-border);
    border-radius: 12px;
    overflow: hidden;
    background: var(--ophq-bg-2);
    display: grid;
    place-items: center;
  }
  .host { position: absolute; inset: 0; }
  .hidden { display: none; }
  .fallback { width: 100%; height: 100%; object-fit: contain; padding: 1rem; box-sizing: border-box; }
  .note { color: var(--ophq-muted); font-size: 0.85rem; z-index: 1; }
</style>
