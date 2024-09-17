<h1 align="center">
  <a href="https://decimer.ai" target="_blank">
    <img src="https://github.com/Kohulan/DECIMER-Image-to-SMILES/raw/master/assets/DECIMER.gif" width="600" alt="DECIMER Logo">
  </a>
</h1>

<h4 align="center">Deep Learning for Chemical Image Recognition - WebApp</h4>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <a href="https://GitHub.com/OBrink/DECIMER_Web/graphs/commit-activity"><img src="https://img.shields.io/badge/Maintained%3F-yes-blue.svg" alt="Maintenance"></a>
  <a href="https://GitHub.com/OBrink/DECIMER_Web/issues/"><img src="https://img.shields.io/github/issues/OBrink/DECIMER_Web.svg" alt="GitHub issues"></a>
  <a href="https://GitHub.com/OBrink/DECIMER_Web/graphs/contributors/"><img src="https://img.shields.io/github/contributors/OBrink/DECIMER_Web.svg" alt="GitHub contributors"></a>
  <br>
  <a href="https://GitHub.com/OBrink/DECIMER_Web/releases/"><img src="https://img.shields.io/github/release/OBrink/DECIMER_Web.svg" alt="GitHub release"></a>
  <a href="https://zenodo.org/badge/latestdoi/486488537"><img src="https://zenodo.org/badge/486488537.svg" alt="DOI"></a>
  <a href="https://www.tensorflow.org"><img src="https://img.shields.io/badge/TensorFlow-2.15.0-FF6F00.svg?style=flat&logo=tensorflow" alt="tensorflow"></a>

<p align="center">
  <a href="#about">About</a> •
  <a href="#installation">Installation</a> •
  <a href="#powered-by">Powered By</a> •
  <a href="#license">License</a> •
  <a href="#citation">Citation</a> •
  <a href="#further-reading">Further Reading</a> •
  <a href="#research-group">Research Group</a>
</p>

<hr>

## About

<p align="center">
  <b>DECIMER (Deep Learning for Chemical Image Recognition)</b>
</p>

This repository contains the code running on [decimer.ai](https://decimer.ai)

🧪 DECIMER is a powerful tool that can:
- Perform automated chemical image segmentation
- Recognize chemical structures in scientific publications
- Convert chemical structure depictions to machine-readable formats

DECIMER is actively developed and maintained by the [Steinbeck group](https://cheminf.uni-jena.de/) at the [Friedrich Schiller University Jena](https://www.uni-jena.de/).

<hr>

## Installation

### How to run DECIMER Web locally

```bash
git clone https://github.com/OBrink/DECIMER.ai.git
sudo chmod -R 777 DECIMER.ai
cd DECIMER.ai/
mv .env.example .env
sed -i '$ d' routes/web.php # Deletes the last line "URL::forceScheme('https');"
sudo chmod -R 777 storage/
sudo chmod -R 777 bootstrap/cache/
docker-compose up --build -d
```

1. Open your browser (Chrome or Chromium-based recommended)
2. Navigate to `http://localhost:80`
3. On first run, generate an app key when prompted
4. Refresh the page
5. Wait 5-10 minutes for all models to load

> 📘 **Note:** Check out the [DECIMER.ai wiki](https://github.com/OBrink/DECIMER.ai/wiki) for advanced setup options and customizations!

<hr>

## Powered By

<p align="center">
  <a href="https://github.com/Kohulan/DECIMER-Image-Segmentation">
    <img src="https://raw.githubusercontent.com/OBrink/DECIMER_Web/main/logos/DECIMER_Segmentation_logo.png" alt="DECIMER Segmentation" width="200"/>
  </a>
  <a href="https://github.com/Kohulan/Smiles-TO-iUpac-Translator">
    <img src="https://github.com/Kohulan/STOUT_WebApp/raw/main/frontend/public/STOUT.png" alt="STOUT" width="200"/>
  </a>
  <a href="https://github.com/Kohulan/DECIMER-Image_Transformer">
    <img src="https://raw.githubusercontent.com/OBrink/DECIMER_Web/main/logos//DECIMER_Transformer_logo.png" alt="DECIMER Transformer" width="200"/>
  </a>
</p>

<hr>

## License :scroll:

This project is licensed under the MIT License - see the [LICENSE](https://github.com/Steinbeck-Lab/DECIMER.ai/blob/main/LICENSE) file for details.

<hr>

## Citation :newspaper:

If you use this work, please cite:

```bibtex
@article{rajan2023decimer,
  title={DECIMER.ai - An open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications},
  author={Rajan, Kohulan and Brinkhaus, Henning Otto and Agea, Maria Inmaculada and Zielesny, Achim and Steinbeck, Christoph},
  journal={ChemRxiv},
  year={2023},
  doi={10.26434/chemrxiv-2023-xhcx9}
}
```

<hr>

## Further Reading :books:

- [DECIMER: towards deep learning for chemical image recognition](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-020-00469-w)
- [DECIMER-Segmentation: Automated extraction of chemical structure depictions from scientific literature](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-021-00496-1)
- [DECIMER 1.0: deep learning for chemical image recognition using transformers](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-021-00538-8)
- [STOUT: SMILES to IUPAC names using neural machine translation](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-021-00510-6)

<hr>

## Research Group

<p align="center">
  <a href="https://cheminf.uni-jena.de">
    <img src="https://github.com/Kohulan/DECIMER-Image-to-SMILES/blob/master/assets/CheminfGit.png" alt="Cheminformatics and Computational Metabolomics Group" width="300"/>
  </a>
</p>

<p align="center">
  🔬 DECIMER is developed and maintained by the <a href="https://cheminf.uni-jena.de">Steinbeck group</a> at the <a href="https://www.uni-jena.de/en/">Friedrich Schiller University Jena</a>, Germany.
</p>

<hr>

<p align="center">
  Made with ❤️ by the <a href="https://cheminf.uni-jena.de">Steinbeck Group</a> 
</p>
