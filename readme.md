# 🛰️ SAR Battle Damage Detector (T-Test)

A geospatial web application for detecting structural damage in conflict zones using **Sentinel-1 Synthetic Aperture Radar (SAR)** and pixel-wise statistical analysis.

Built with **Google Earth Engine**, **Streamlit**, and **Geemap**.

---

## 🚀 Overview
Traditional optical change detection is often hindered by clouds, smoke, or nighttime. This tool utilizes **Synthetic Aperture Radar (SAR)**, which penetrates atmospheric interference to detect permanent physical changes in structures.

By performing a **Welch's T-Test** on time-series radar stacks, the app identifies statistically significant drops in backscatter that correlate with building destruction, while filtering out seasonal or atmospheric noise.

### Key Features:
* **Custom Time Windows:** Compare "Baseline" (pre-war) and "Assessment" (post-war) periods.
* **Multi-Source Footprints:** Choose between **OpenStreetMap**, **Google Open Buildings**, or **Global Building Atlas** to mask results to human structures.
* **Population Impact:** Estimate the human cost by overlaying damage maps with **WorldPop** 100m population data.
* **Interactive T-Test:** Dynamic thresholding (T > 3.5) to separate real damage from radar "speckle."

---

## 🛠️ Installation & Local Setup

1. **Clone the repo:**
   ```bash
   git clone [https://github.com/your-username/battle-damage-detector.git](https://github.com/your-username/battle-damage-detector.git)
   cd battle-damage-detector
