# Image Placeholders for KANDA M.Tech Report

## Instructions
Add images to `/kanda/overleaf/images/` folder and uncomment the `\includegraphics` commands in the respective chapters.

---

## Chapter 1 — Introduction

### Figure 1.1: KANDA Robot Assembly (optional)
**Location:** `chapter1.tex` — no figure environment (avoids empty placeholder clutter)  
**Filename to use:** `kanda_robot.jpg` or `kanda_assembled.png`  
**Current:** Chapter~1 references Fig.~\ref{fig:hw_block} in Chapter~3. Add a figure in Ch.~1 only when the photo exists.

---

## Chapter 4 — High Level Design

### Figure 4.1: Safety Validation Pipeline
**Location:** `chapter4.tex`, line ~74  
**Current:** TikZ diagram (works, no image needed yet)  
**Optional filename:** `safety_pipeline_diagram.png`  
**Alternative:** If you want to replace TikZ with image, uncomment lines in chapter4.tex (search for "IMAGE PLACEHOLDER")

### Figure 4.2: System Architecture
**Location:** `chapter4.tex`, line ~109  
**Current:** TikZ diagram (works, no image needed yet)  
**Optional filename:** `system_architecture_diagram.png`

---

## Chapter 5 — Detailed Design

### Figure 5.1: High-Level Deployable Blocks
**Location:** `chapter5.tex`, line ~16  
**Current:** TikZ diagram  
**Placeholder comment:** Lines 16-19 show how to replace with image  
**Optional filename:** `deployment_architecture.png`

### Figure 5.X: Control Flow Diagram
**Suggested filename:** `control_flow_diagram.png`  
**Suggested placement:** After discussing "One Control Cycle"

### Figure 5.X: Pi Module Structure
**Suggested filename:** `python_modules_structure.png`  
**Suggested placement:** After Python module description

---

## Chapter 6 — Implementation

### Figure 6.X: Repository Layout Tree
**Suggested filename:** `repo_layout.png`  
**Suggested placement:** After "Repository and Module Layout" section

### Figure 6.X: Serial Protocol Format
**Suggested filename:** `serial_protocol_diagram.png`  
**Suggested placement:** After protocol description

### Figure 6.X: Firmware State Machine
**Suggested filename:** `firmware_state_machine.png`  
**Suggested placement:** In "Embodiment Firmware" subsection

---

## Chapter 7 — Software Testing

No figure placeholders (terminal and OLED visuals not required).

### Figure 7.1: Timing Measurements Chart (optional)
**Suggested filename:** `timing_results_chart.png`  
**Suggested dimensions:** Graph showing latencies for wake-word, intent, plan, VLM

### Figure 7.2: Search Trajectory
**Suggested filename:** `search_trajectory_map.png`  
**Suggested placement:** In object search results

### Figure 7.3: Ablation Study Visualization
**Suggested filename:** `ablation_results_chart.png`  
**Suggested placement:** After ablation table

---

## Chapter 8 — Future Work & Conclusions

### Figure 8.x: Robot in action (removed)
**Was:** `kanda_side.jpg` placeholder in Ch.~8 — removed to avoid duplicate empty box; use Ch.~3 hardware diagram or add one photo in Ch.~1 when available.

### Figure 8.X: Planned Enhancements Roadmap
**Suggested filename:** `future_roadmap.png`  
**Suggested placement:** In "Future Improvements" section

---

## How to Add Images

1. **Save image to folder:**
   ```
   cp /path/to/image.png /Users/sts/darsh/kanda/overleaf/images/
   ```

2. **Uncomment in LaTeX:**
   Find the placeholder line (e.g., `% \includegraphics[width=...]{filename.png}`) and uncomment it

3. **OR Add new figure:**
   ```latex
   \begin{figure}[H]
   \centering
   \includegraphics[width=0.9\textwidth]{filename.png}
   \caption{Figure caption here}
   \label{fig:unique_label}
   \end{figure}
   ```

---

## Current Status

✅ **Already humanized:**
- Chapter 1: Introduction (Intro updated, robot placeholder ready)
- Chapter 2: Theory and Concepts (Conversational tone added)
- Chapter 3: SRS (Model updated to gemini-2.5-flash-lite)
- Chapter 4: High Level Design (Humanized, TikZ diagrams functional)
- Chapter 5: Detailed Design (Humanized, model updated, deployment placeholder ready)
- Chapter 6: Implementation (Humanized, model updated)

⏳ **Still to humanize:**
- Chapter 7: Results & Testing
- Chapter 8: Future Work & Conclusions  
- Chapter 9: References

---

## Image Format Recommendations

- **PNG format:** Best for diagrams, screenshots
- **JPG format:** Best for photos
- **Resolution:** 300 DPI for print quality
- **Width:** Use `0.9\textwidth` for full-width figures, `0.5\textwidth` for side-by-side
- **Aspect ratio:** Match source to avoid distortion

---

## Mermaid Diagrams Available

The following Mermaid diagrams are ready to be converted to PNG and embedded:

1. **Data Flow Diagram** → `dataflow_diagram.png`
2. **State Machine (7 states)** → `state_machine_colored.png`
3. **Architecture (3 tiers)** → `architecture_3tier.png`

Convert at: https://mermaid.live (paste Mermaid code, download as PNG)
