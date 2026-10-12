/* OCR lifecycle controller for the scan-to-check ingredient-label photo path.
 *
 * Regulatory information, not legal advice.
 *
 * A SMALL, focused controller that owns every part of the OCR lifecycle in one
 * place: image selection/validation, the preview, pending worker
 * initialization, the resolved worker, the active recognition job, progress and
 * error callbacks, and object-URL / worker-termination cleanup.
 *
 * Design goals (from the independent review that motivated this split):
 *   1. ONE immutable session identity binds the chosen File, its preview and its
 *      recognition job. Every file selection -- including empty, invalid or
 *      oversized replacements -- retires any earlier validation/recognition, so
 *      a stale completion can never populate a newer draft or enable a newer
 *      invalid file.
 *   2. Every asynchronous operation is an ATTEMPT that captures its own
 *      immutable session id and is only allowed to touch the current worker,
 *      status, controls and busy flags while it is still the active owner.
 *      Old success/error/progress/finally callbacks never mutate a newer
 *      session. Recognition is bound to the controller's validated File (and
 *      its object URL), never reread from an unchecked DOM file input.
 *   3. Real asset-failure handling is bounded AND owned. A worker/core/model
 *      load error is surfaced as an actionable message (manual fallback +
 *      retry) and never leaves the UI stuck at "Loading OCR engine…". Because
 *      Tesseract.js v6.0.1 does NOT settle its createWorker promise on a
 *      `loadLanguage` (traineddata) failure, the controller captures the native
 *      Web Worker that Tesseract spawns (via a narrow, version-coupled
 *      compatibility adapter that temporarily replaces the global `Worker`
 *      constructor only for the synchronous createWorker call and restores it
 *      before returning) and terminates THAT handle directly, so no orphan
 *      native Worker accumulates.
 *   4. Empty recognition is reported as "No text found", never as a successful
 *      ingredient extraction; the user still gets the editable-draft path.
 *
 * Worker ownership policy (intentional + documented): the worker is created
 * lazily and REUSED for a retry within the current session; it is DISPOSED on
 * any session change (new image, cancel, mode change, page exit, dispose) and
 * on any real recognition failure (so a poisoned worker is not reused). Thus no
 * worker survives a stale session and no unbounded set of workers accumulates.
 * Object URLs are always released on session retirement.
 *
 * The controller is dependency-free: it receives the Tesseract factory and the
 * DOM element handles from the caller, so the real engine and the deterministic
 * browser test stub exercise the SAME lifecycle code. It never silently submits
 * OCR output or infers concentrations.
 */
(function (global) {
  "use strict";

  var OCR_MAX_BYTES = 10 * 1024 * 1024;   // bounded uploaded image size
  var OCR_MAX_DIM = 6000;                 // bounded decoded image dimension
  var CACHE_METHOD = "none";              // never persist traineddata to IndexedDB
  var INIT_TIMEOUT_MS = 30000;            // bounded engine load

  // Pinned vendored OCR assets (no runtime CDN; same-origin). corePath is a
  // DIRECTORY so Tesseract picks a SIMD/LSTM variant (upstream guidance).
  var OCR_ASSETS = {
    workerPath: "/static/vendor/tesseract/worker.min.js",
    corePath: "/static/vendor/tesseract/core",
    langPath: "/static/vendor/tesseract",
    lang: "eng",
  };

  function createOcrController(opts) {
    opts = opts || {};
    var Tesseract = opts.tesseract;             // factory injected by the caller
    var els = opts.els || {};                   // { preview, run, cancel, file }
    var statusEl = opts.status;                 // status text element
    var onChange = opts.onChange || function () {};

    // --- ownership state ----------------------------------------------------
    //
    // ``session`` is an IMMUTABLE generation counter. It is incremented on every
    // retirement boundary (open, selectFile, cancel, dispose). Every attempt
    // (init or recognition) snapshots the session it was created under and is
    // only ever allowed to touch the UI while that session is still current.
    // ``initAttempt`` / ``recogAttempt`` are the single active owners; a stale
    // attempt compares itself against these (and against ``session``) and no-ops.
    var session = 0;                 // current immutable session id
    var disposed = false;
    var file = null;                 // validated File bound to the current session
    var objectUrl = null;            // object URL bound to the current session
    var workerInstance = null;       // { api, native, session, disposed }
    var initSeq = 0;                 // monotonic -- identifies an init attempt
    var initAttempt = null;          // the single active initialization attempt
    var recogSeq = 0;                // monotonic -- identifies a recognition attempt
    var recogAttempt = null;         // the single active recognition attempt

    function isCurrentSession(sid) { return sid === session && !disposed; }

    function setStatus(text) { if (statusEl) statusEl.textContent = text || ""; }
    function setRun(enabled) { if (els.run) els.run.disabled = !enabled; }
    function setCancel(enabled) { if (els.cancel) els.cancel.disabled = !enabled; }

    function releasePreview() {
      if (objectUrl) {
        try { URL.revokeObjectURL(objectUrl); } catch (e) { /* already gone */ }
        objectUrl = null;
      }
      if (els.preview) {
        els.preview.removeAttribute("src");
        els.preview.hidden = true;
      }
    }

    // Dispose of an API worker handle (the Tesseract resolveObj). Swallow a
    // rejected termination promise.
    function terminateSafely(handle) {
      if (!handle) return;
      try {
        var p = handle.terminate();
        if (p && typeof p.catch === "function") p.catch(function () {});
      } catch (e) { /* already gone */ }
    }

    // Directly terminate the captured native Web Worker. This is the only way to
    // release the underlying worker when Tesseract's createWorker promise never
    // settles (e.g. a traineddata 404 leaves the promise hanging).
    function terminateNative(nativeHandle) {
      if (!nativeHandle) return;
      try { nativeHandle.terminate(); } catch (e) { /* already gone */ }
    }

    // Dispose a worker record, preferring the API handle (which itself calls the
    // native terminate) and falling back to the captured native handle.
    function disposeWorker(inst) {
      if (!inst || inst.disposed) return;
      inst.disposed = true;
      if (inst.api) terminateSafely(inst.api);
      else terminateNative(inst.native);
    }

    // Retire the current session: invalidate any in-flight init/recognition and
    // immediately dispose the underlying worker(s) so nothing leaks.
    function retire() {
      session++;
      if (initAttempt) {
        initAttempt.settled = true;               // its callbacks now no-op
        if (initAttempt.timer) clearTimeout(initAttempt.timer);
        // Terminate the native handle even if createWorker is still hanging.
        var native = initAttempt.nativeHandle && initAttempt.nativeHandle();
        terminateNative(native);
        initAttempt = null;
      }
      disposeWorker(workerInstance); workerInstance = null;
      recogAttempt = null;
      file = null;
      releasePreview();
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      retire();
    }

    function loadImageMetadata(fileObj) {
      return new Promise(function (resolve, reject) {
        var img = new Image();
        var url = URL.createObjectURL(fileObj);
        img.onload = function () {
          URL.revokeObjectURL(url);
          resolve({ width: img.naturalWidth || img.width,
                    height: img.naturalHeight || img.height });
        };
        img.onerror = function () {
          URL.revokeObjectURL(url);
          reject(new Error("unreadable image"));
        };
        img.src = url;
      });
    }

    // Validate + preview the selected file under a NEW session identity. Any
    // prior session is retired first, so replacing A with an invalid/empty file
    // cannot leave A's result or A's valid image active. Recognize is disabled
    // during validation, and a late validation for a retired session cannot
    // enable a newer invalid file.
    function selectFile(fileObj) {
      retire();                  // fresh session; release any prior worker
      var id = session;
      setRun(false);             // disabled during validation
      if (!fileObj) { setStatus("No image selected."); return; }
      if (!/^image\/(jpeg|png)$/i.test(fileObj.type)) {
        setStatus("Unsupported image type (use JPEG or PNG).");
        return;
      }
      if (fileObj.size > OCR_MAX_BYTES) {
        setStatus("Image too large (max " + (OCR_MAX_BYTES / 1048576) + " MB).");
        return;
      }
      loadImageMetadata(fileObj).then(function (meta) {
        if (!isCurrentSession(id)) return;       // replaced while validating
        if (meta.width > OCR_MAX_DIM || meta.height > OCR_MAX_DIM) {
          setStatus("Image too large (" + meta.width + "x" + meta.height +
                    " px). Use a smaller image.");
          releasePreview();
          return;
        }
        file = fileObj;
        objectUrl = URL.createObjectURL(fileObj);
        if (els.preview) {
          els.preview.onload = function () {};
          els.preview.src = objectUrl;
          els.preview.hidden = false;
        }
        setRun(true);
        setStatus("Image selected. Click 'Recognize text' to run local OCR.");
        onChange({ state: "selected", file: fileObj, objectUrl: objectUrl });
      }).catch(function () {
        if (isCurrentSession(id)) { setStatus("Could not read the selected image."); setRun(false); }
      });
    }

    // Narrow, documented, TEMPORARILY SCOPED adapter. Tesseract's createWorker
    // spawns exactly one native Worker synchronously before returning. We
    // temporarily replace the global `Worker` constructor ONLY for the duration
    // of that synchronous call (restored in the ``finally`` below) so we can
    // capture the exact native Worker instance it creates. This is a
    // version-coupled compatibility adapter for the pinned Tesseract 6.0.1
    // (whose createWorker promise never settles on a loadLanguage failure): it
    // lets us terminate the underlying worker even when that promise hangs, so
    // no orphan native Worker accumulates. It is NOT a general Worker
    // abstraction; the global constructor is restored before createWorker
    // returns and never left replaced in production.
    function createTracked(factory, args) {
      var capturedNative = null;
      var NativeWorker = global.Worker;
      if (typeof NativeWorker === "function") {
        global.Worker = function Tracked(url, options) {
          var real = new NativeWorker(url, options);
          capturedNative = real;       // exactly the worker Tesseract spawns
          return real;
        };
      }
      var error = null;
      var promise;
      try {
        promise = factory.createWorker.apply(factory, args);
      } catch (e) {
        error = e;
      } finally {
        if (typeof NativeWorker === "function") global.Worker = NativeWorker;
      }
      return {
        promise: promise,
        native: function () { return capturedNative; },
        error: error,
      };
    }

    // Lazily create (and CACHE for a retry) the Tesseract worker against the
    // pinned assets. Only one initialization runs at a time. Bounded by an
    // explicit timeout; a late-created worker is disposed (never assigned as the
    // current owner) if the session was retired/failed/timed-out while it was
    // still loading.
    function ensureWorker() {
      if (workerInstance && workerInstance.session === session && !workerInstance.disposed) {
        return Promise.resolve(workerInstance.api);
      }
      if (typeof Tesseract !== "object" || typeof Tesseract.createWorker !== "function") {
        return Promise.reject(new Error("Tesseract.js failed to load (asset missing)."));
      }
      var sid = session;
      var attempt = {
        id: ++initSeq, session: sid, settled: false, timer: null, nativeHandle: null,
      };
      initAttempt = attempt;
      setStatus("Loading OCR engine…");

      var logger = function (m) {
        // Progress is surfaced only while THIS attempt is still the owner and the
        // session is still current. A stale attempt's logger never touches the UI.
        if (attempt === initAttempt && isCurrentSession(attempt.session) &&
            m && m.status === "recognizing text") {
          setStatus("Recognizing… " + (Math.round((m.progress || 0) * 100)) + "%");
        }
      };

      var createResolve, createReject;
      var resultPromise = new Promise(function (res, rej) {
        createResolve = res; createReject = rej;
      });

      function disposeInitNative() {
        if (attempt.nativeHandle) terminateNative(attempt.nativeHandle());
      }

      function failInit(err) {
        if (attempt.settled) return;
        attempt.settled = true;
        if (attempt.timer) clearTimeout(attempt.timer);
        if (initAttempt === attempt) initAttempt = null;
        // Terminate the native worker even though the createWorker promise may
        // never settle (this is the actual runtime disposal).
        disposeInitNative();
        setCancel(true);
        createReject(err);
      }

      // The library reports job rejects through errorHandler for action 'load',
      // 'loadLanguage' (traineddata) and 'recognize'. Only treat this as a
      // FAIL-FAST initialization error while the init attempt is still pending;
      // after init settles, a recognize reject is handled by the recognize
      // promise, not here.
      var errorHandler = function (err) {
        if (attempt.settled) return;
        var msg = (typeof err === "string" && err) || (err && err.message) ||
                  "OCR engine failed to load.";
        failInit(new Error(msg));
      };

      var tracked = createTracked(Tesseract, [OCR_ASSETS.lang, 1, {
        workerPath: OCR_ASSETS.workerPath,
        corePath: OCR_ASSETS.corePath,
        langPath: OCR_ASSETS.langPath,
        gzip: true,
        cacheMethod: CACHE_METHOD,   // do not persist traineddata to IndexedDB
        logger: logger,
        errorHandler: errorHandler,
      }]);
      attempt.nativeHandle = tracked.native;
      if (tracked.error) {
        attempt.settled = true;
        if (initAttempt === attempt) initAttempt = null;
        disposeInitNative();
        setRun(false); setCancel(true);
        return Promise.reject(tracked.error);
      }
      var createPromise = tracked.promise;

      attempt.timer = setTimeout(function () {
        if (attempt.settled) return;
        failInit(new Error("OCR engine did not load (timeout). Check the connection and try again."));
      }, INIT_TIMEOUT_MS);

      createPromise.then(function (created) {
        if (attempt.timer) clearTimeout(attempt.timer);
        if (attempt.settled) {
          // This init already failed/timed-out/retired; dispose the late worker.
          terminateSafely(created);
          disposeInitNative();
          return;
        }
        attempt.settled = true;
        if (initAttempt === attempt) initAttempt = null;
        if (!isCurrentSession(attempt.session)) {
          terminateSafely(created);
          disposeInitNative();
          createReject(new Error("OCR session changed."));
          return;
        }
        workerInstance = {
          api: created,
          native: attempt.nativeHandle ? attempt.nativeHandle() : null,
          session: attempt.session,
          disposed: false,
        };
        setStatus("OCR engine ready.");
        createResolve(created);
      }, function (err) {
        if (attempt.timer) clearTimeout(attempt.timer);
        if (attempt.settled) return;
        attempt.settled = true;
        if (initAttempt === attempt) initAttempt = null;
        disposeInitNative();
        setCancel(true);
        if (!isCurrentSession(attempt.session)) { createReject(new Error("OCR session changed.")); return; }
        createReject(err);
      });

      return resultPromise;
    }

    // Bounded recognition of the current session's validated object URL.
    function recognizeCurrent() {
      var w = workerInstance;
      if (!w || w.disposed || w.session !== session) {
        return Promise.reject(new Error("OCR engine not loaded."));
      }
      if (recogAttempt) return Promise.reject(new Error("Recognition already running."));
      var sid = session;
      var attempt = { id: ++recogSeq, session: sid, cancelled: false };
      recogAttempt = attempt;
      setRun(false);
      setCancel(true);
      setStatus("Recognizing text…");
      var target = objectUrl;
      return w.api.recognize(target).then(function (ret) {
        if (recogAttempt === attempt) recogAttempt = null;
        if (!isCurrentSession(attempt.session)) throw new Error("OCR session changed.");
        if (attempt.cancelled) throw new Error("OCR cancelled.");
        var text = (ret && ret.data && (ret.data.text || "")) || "";
        return { text: text, empty: !text.trim(), attempt: attempt };
      }, function (err) {
        if (recogAttempt === attempt) recogAttempt = null;
        if (!isCurrentSession(attempt.session)) throw new Error("OCR session changed.");
        // A real recognition failure may have poisoned the worker; dispose it so
        // a retry starts clean rather than reusing a broken worker.
        disposeWorker(workerInstance); workerInstance = null;
        throw err;
      });
    }

    function run() {
      // Recognition is bound to the controller's validated selection, not to a
      // re-read DOM file (which could be a newer, unvalidated file).
      if (!file) { setStatus("No image selected."); return Promise.reject(new Error("No image selected.")); }
      var sid = session;
      setRun(false);
      setCancel(true);
      var workerPromise = workerInstance ? Promise.resolve(workerInstance.api) : ensureWorker();
      return workerPromise.then(function () {
        if (!isCurrentSession(sid)) throw new Error("OCR session changed.");
        return recognizeCurrent();
      }).then(function (res) {
        if (!isCurrentSession(res.attempt.session)) throw new Error("OCR session changed.");
        onChange({ state: "recognized", text: res.text, empty: res.empty });
        if (res.empty) setStatus("No text found. Try a clearer image or use manual entry.");
        else setStatus("");
        return res;
      }).catch(function (err) {
        return _surfaceError(err, sid);
      });
    }

    function _surfaceError(err, sid) {
      var msg = (err && err.message) || "unknown";
      var cancelled = msg === "OCR cancelled." || msg === "OCR session changed.";
      // Only mutate controls/status if this attempt's session is still current;
      // a stale completion must NOT enable a newer invalid file or overwrite a
      // newer status.
      if (isCurrentSession(sid)) {
        setStatus(cancelled ? "OCR cancelled." : "OCR failed: " + msg);
        setRun(!!file);
        setCancel(true);
      }
      return Promise.reject(err);
    }

    function open() {
      // Re-arm the controller (a prior dispose() must not prevent reopening).
      disposed = false;
      retire();                  // fresh session; release any prior worker
      setRun(false);
      setCancel(true);
      setStatus("Choose a JPEG/PNG image of the ingredient label.");
    }

    function cancel() {
      retire();                  // dispose the worker (per-session policy)
      setRun(false);
      setCancel(true);
      setStatus("OCR cancelled.");
    }

    return {
      open: open,
      selectFile: selectFile,
      recognize: run,
      cancel: cancel,
      dispose: dispose,
      isBusy: function () {
        return !!(recogAttempt) || !!(initAttempt && !initAttempt.settled);
      },
      hasWorker: function () { return !!workerInstance; },
    };
  }

  global.ScanToCheckOcr = { create: createOcrController };
})(typeof self !== "undefined" ? self : this);
