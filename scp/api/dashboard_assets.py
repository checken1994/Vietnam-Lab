"""
[Task 8-A] Dashboard Canvas & Input Controls JS — extracted from dashboard_html.py
"""

DASHBOARD_CANVAS_JS = """
    // --- 4. RENDER LOOP (HIGH-PERFORMANCE CANVAS 60FPS) ---
    const canvas = document.getElementById('neuralCanvas');
    const ctx = canvas.getContext('2d');

    function resizeCanvas() {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    // Spawn Particles (Các hạt photon bay dọc synapse)
    function spawnParticle(srcNode, dstNode, color) {
      particles.push({
        x: srcNode.x,
        y: srcNode.y,
        startX: srcNode.x,
        startY: srcNode.y,
        targetX: dstNode.x,
        targetY: dstNode.y,
        progress: 0,
        speed: 0.015 + Math.random() * 0.02,
        color: color || srcNode.color,
        size: 2.2 + Math.random() * 1.5,
        targetNeuron: dstNode
      });
    }

    // Spreading Activation Loop
    function triggerSpreadingActivation() {
      if (!isLearningActive) return;

      // Chọn ngẫu nhiên một số nơ-ron có activation cao để bắn sang tầng sau
      const activeNeurons = neurons.filter(n => n.activation > 0.45);
      activeNeurons.forEach(src => {
        const outgoing = synapses.filter(s => s.from === src.id);
        if (outgoing.length > 0 && Math.random() < 0.3) {
          const s = outgoing[Math.floor(Math.random() * outgoing.length)];
          spawnParticle(s.srcNode, s.dstNode, s.srcNode.color);
          s.active = true;
          s.pulseProgress = 1.0;
        }
      });

      // Tầng Input liên tục nhận tín hiệu nhẹ
      const inputNeurons = neurons.filter(n => n.layerId === 0);
      if (Math.random() < 0.4) {
        const randIn = inputNeurons[Math.floor(Math.random() * inputNeurons.length)];
        randIn.activation = Math.min(1.0, randIn.activation + 0.6);
        randIn.pulseTimer = 1.0;
      }
    }

    function render() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      ctx.save();
      // Apply Camera Transform
      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.scale(camera.zoom, camera.zoom);
      ctx.translate(-canvas.width / 2 + camera.x, -canvas.height / 2 + camera.y);

      // 1. Vẽ cột & tiêu đề từng Layer
      LAYER_DEFS.forEach(l => {
        const layerX = 120 + l.id * 280;
        
        // Đường trục dọc mờ
        ctx.beginPath();
        ctx.strokeStyle = "rgba(30, 41, 59, 0.4)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 6]);
        ctx.moveTo(layerX, 60);
        ctx.lineTo(layerX, 980);
        ctx.stroke();
        ctx.setLineDash([]);

        // Tên tầng
        ctx.fillStyle = l.color;
        ctx.font = "bold 11px monospace";
        ctx.fillText(l.name, layerX - 40, 50);

        ctx.fillStyle = "#64748b";
        ctx.font = "9px monospace";
        ctx.fillText(`Neurons: ${l.count}`, layerX - 40, 65);
      });

      // 2. Vẽ các Synapses (Dây nối vector uốn lượn có trọng số)
      synapses.forEach(s => {
        const src = s.srcNode;
        const dst = s.dstNode;

        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        
        // Đường cong Bezier mượt mà
        const midX = (src.x + dst.x) / 2;
        ctx.bezierCurveTo(midX, src.y, midX, dst.y, dst.x, dst.y);

        if (s.pulseProgress > 0) {
          ctx.strokeStyle = src.color;
          ctx.lineWidth = 1.8;
          ctx.globalAlpha = 0.85 * s.pulseProgress;
          s.pulseProgress -= 0.03;
        } else {
          ctx.strokeStyle = s.isSkip ? "rgba(244, 63, 94, 0.22)" : "rgba(71, 85, 105, 0.16)";
          ctx.lineWidth = Math.max(0.6, Math.min(2.0, (s.weight + 0.5) * 1.2));
          ctx.globalAlpha = Math.max(0.08, Math.min(0.4, (s.weight + 0.4) * 0.3));
        }

        ctx.stroke();
        ctx.globalAlpha = 1.0;
      });

      // 3. Cập nhật & Vẽ các Hạt Tín Hiệu (Particles / Photons)
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.progress += p.speed;

        // Nội suy Bezier cho hạt bay dọc đường cong
        const midX = (p.startX + p.targetX) / 2;
        const t = p.progress;
        const cx1 = midX, cy1 = p.startY;
        const cx2 = midX, cy2 = p.targetY;
        
        // Công thức Cubic Bezier
        const bx = (1 - t)**3 * p.startX + 3 * (1 - t)**2 * t * cx1 + 3 * (1 - t) * t**2 * cx2 + t**3 * p.targetX;
        const by = (1 - t)**3 * p.startY + 3 * (1 - t)**2 * t * cy1 + 3 * (1 - t) * t**2 * cx2 + t**3 * p.targetY;

        ctx.beginPath();
        ctx.arc(bx, by, p.size, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 8;
        ctx.fill();
        ctx.shadowBlur = 0;

        if (p.progress >= 1.0) {
          // Khi hạt tới nơi, kích hoạt nơ-ron đích
          if (p.targetNeuron) {
            p.targetNeuron.activation = Math.min(1.0, p.targetNeuron.activation + 0.4);
            p.targetNeuron.pulseTimer = 1.0;
          }
          particles.splice(i, 1);
        }
      }

      // 4. Vẽ các Nơ-ron (Neurons / Circles with Glow)
      neurons.forEach(n => {
        // Suy hao dần activation về mức nền
        n.activation = Math.max(0.05, n.activation * 0.985);
        if (n.pulseTimer > 0) n.pulseTimer -= 0.04;

        const isHovered = hoveredNeuron && hoveredNeuron.id === n.id;
        const isSelected = selectedNeuron && selectedNeuron.id === n.id;

        // Vòng hào quang bên ngoài khi active
        if (n.activation > 0.3 || isHovered || isSelected) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.radius + 5 + n.activation * 6, 0, Math.PI * 2);
          ctx.fillStyle = n.color;
          ctx.globalAlpha = isSelected ? 0.45 : (isHovered ? 0.35 : n.activation * 0.25);
          ctx.fill();
          ctx.globalAlpha = 1.0;
        }

        // Lõi Nơ-ron
        ctx.beginPath();
        ctx.arc(n.x, n.y, isSelected ? n.radius + 3 : (isHovered ? n.radius + 2 : n.radius), 0, Math.PI * 2);
        ctx.fillStyle = n.activation > 0.5 ? "#ffffff" : n.color;
        ctx.shadowColor = n.color;
        ctx.shadowBlur = isSelected ? 16 : (isHovered ? 12 : n.activation * 10);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Viền nơ-ron
        ctx.beginPath();
        ctx.arc(n.x, n.y, isSelected ? n.radius + 3 : (isHovered ? n.radius + 2 : n.radius), 0, Math.PI * 2);
        ctx.strokeStyle = isSelected ? "#38bdf8" : (isHovered ? "#ffffff" : "rgba(255,255,255,0.4)");
        ctx.lineWidth = isSelected ? 2.5 : 1.2;
        ctx.stroke();

        // Nhãn chữ nhỏ bên cạnh nơ-ron
        if (camera.zoom > 0.75) {
          ctx.fillStyle = isSelected ? "#38bdf8" : (isHovered ? "#ffffff" : "rgba(203, 213, 225, 0.55)");
          ctx.font = isSelected || isHovered ? "bold 9.5px monospace" : "8.5px monospace";
          ctx.fillText(n.fullName, n.x + 10, n.y + 3);
        }
      });

      ctx.restore();

      // Kích hoạt chu kỳ xung thần kinh
      if (Math.random() < 0.25) {
        triggerSpreadingActivation();
      }

      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    // --- 5. MOUSE & TOUCH CONTROLS (PAN, ZOOM, HOVER, CLICK) ---
    function screenToWorld(sx, sy) {
      const cx = canvas.width / 2;
      const cy = canvas.height / 2;
      const wx = (sx - cx) / camera.zoom + cx - camera.x;
      const wy = (sy - cy) / camera.zoom + cy - camera.y;
      return { x: wx, y: wy };
    }

    canvas.addEventListener('mousedown', (e) => {
      isDragging = true;
      dragStart = { x: e.clientX, y: e.clientY };
    });

    window.addEventListener('mousemove', (e) => {
      if (isDragging) {
        camera.x += (e.clientX - dragStart.x) / camera.zoom;
        camera.y += (e.clientY - dragStart.y) / camera.zoom;
        dragStart = { x: e.clientX, y: e.clientY };
        return;
      }

      // Hit-test nơ-ron khi hover
      const world = screenToWorld(e.clientX, e.clientY);
      let found = null;
      for (const n of neurons) {
        const dx = world.x - n.x;
        const dy = world.y - n.y;
        if (dx * dx + dy * dy <= (n.radius + 8) ** 2) {
          found = n;
          break;
        }
      }

      hoveredNeuron = found;
      const tooltip = document.getElementById('neuron-tooltip');
      if (found) {
        tooltip.style.left = `${e.clientX + 16}px`;
        tooltip.style.top = `${e.clientY + 16}px`;
        tooltip.classList.remove('hidden');

        document.getElementById('tt-name').textContent = found.fullName;
        document.getElementById('tt-layer').textContent = found.layerName;
        document.getElementById('tt-kind').textContent = found.layerName.split(' ')[0].toLowerCase();
        document.getElementById('tt-act').textContent = found.activation.toFixed(3);
        document.getElementById('tt-fitness').textContent = found.fitness.toFixed(3);
        document.getElementById('tt-age').textContent = `${found.age} epoch`;
        
        const conns = synapses.filter(s => s.from === found.id || s.to === found.id).length;
        document.getElementById('tt-conn').textContent = conns;
        document.getElementById('tt-file').textContent = found.file;
      } else {
        tooltip.classList.add('hidden');
      }
    });

    window.addEventListener('mouseup', () => {
      isDragging = false;
    });

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.12 : 0.88;
      camera.zoom = Math.max(0.35, Math.min(2.8, camera.zoom * zoomFactor));
    }, { passive: false });

    // Click nơ-ron mở Drawer
    canvas.addEventListener('click', (e) => {
      const world = screenToWorld(e.clientX, e.clientY);
      for (const n of neurons) {
        const dx = world.x - n.x;
        const dy = world.y - n.y;
        if (dx * dx + dy * dy <= (n.radius + 10) ** 2) {
          selectNeuron(n);
          return;
        }
      }
    });

    // Double-click reset view
    canvas.addEventListener('dblclick', () => {
      camera = { x: 0, y: 0, zoom: 0.85 };
    });

    function selectNeuron(n) {
      selectedNeuron = n;
      n.activation = 1.0;
      n.pulseTimer = 1.0;

      // Mở Drawer bên phải
      const drawer = document.getElementById('inspector-drawer');
      drawer.classList.remove('hidden');

      document.getElementById('ins-layer-badge').textContent = `LAYER ${n.layerId}: ${n.layerName}`;
      document.getElementById('ins-node-name').textContent = n.fullName;
      document.getElementById('ins-desc').textContent = n.desc;
      document.getElementById('ins-file').textContent = n.file;
      document.getElementById('ins-in-schema').textContent = n.inSchema;
      document.getElementById('ins-out-schema').textContent = n.outSchema;

      // Bắn xung sang các kết nối lân cận
      const outgoing = synapses.filter(s => s.from === n.id);
      outgoing.forEach(s => spawnParticle(s.srcNode, s.dstNode, s.srcNode.color));
    }

    function closeInspector() {
      document.getElementById('inspector-drawer').classList.add('hidden');
      selectedNeuron = null;
    }

    function triggerNeuronFire() {
      if (!selectedNeuron) return;
      selectedNeuron.activation = 1.0;
      const outgoing = synapses.filter(s => s.from === selectedNeuron.id);
      outgoing.forEach(s => spawnParticle(s.srcNode, s.dstNode, selectedNeuron.color));
    }
"""
