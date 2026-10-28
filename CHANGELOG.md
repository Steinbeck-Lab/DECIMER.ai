# Changelog

## 1.0.0 (2026-10-28)


### Features

* **api:** discover model replicas, send each call to the least busy one, schedule jobs fairly ([dfebe38](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/dfebe38201ec6832c02dc6e71a0b67be2a6d1545))
* **api:** fall back to CDK for SMILES that RDKit refuses and report which toolkit accepted them ([8ec9c57](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/8ec9c57288d819569fe4f9fdbe1f56f797e06a9c))
* **api:** job API core with uploads, job status and limits ([d531db9](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/d531db9d205213fe09f4c0e22da530b7a62ef9a1))
* **api:** read a single tightly cropped image whole instead of the segmented crop ([e83b72e](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/e83b72e670c705a89a9034751422677b2c8caae8))
* **api:** read each image of a multi-image upload whole, as the earlier version did ([b7500be](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/b7500bef7a8edc63988d087e7d56c773d3b9f5e8))
* **api:** validate, export, problem report, engines and job delete endpoints ([603e231](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/603e231987348dd5b0bebb03321d3f2a0ec24343))
* **api:** worker pipeline that renders, segments, recognises and checks each job ([2e6a6b8](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/2e6a6b8a1aeb82cfdb34cfa623803dbf5952bebd))
* **chem:** CDK molblocks for SMILES that RDKit refuses ([58ad5ec](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/58ad5ec98734911bc2a71d9bce44ca189c3d41ef))
* **chem:** chemistry service with checked IUPAC names from Orthonym ([3d99d10](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/3d99d10f08c40894a9ddef415bedc43e365db32c))
* **cls:** image classifier service ([d6e47cd](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/d6e47cd4fb84132c5f7a81db6a3c6292f9099556))
* **deploy:** Dockerfiles, Compose project and nginx edge ([1fa3c2f](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/1fa3c2f63ab76c4e3b54c3837e459ec78a064f3f))
* **deploy:** front door configuration and uptime check ([d65fdd8](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/d65fdd80d3b275e847a45d9bffc8a3cf6efba850))
* **deploy:** model replicas, thread caps and memory limits per service ([9c7c7f7](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/9c7c7f7530d27a1248ebdc0c292eaefb75515016))
* **deploy:** run more ocsr replicas by default ([b3d14c1](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/b3d14c1c13b3872bf467a766fe2a43306f334320))
* error reporting with self-hosted GlitchTip, JSON logs, usage counter and an isolated ingest sidecar ([5d144c5](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/5d144c5dab24b5a7131d9afc0c3c4b894291e56d))
* **golden:** golden-set paper, labels and the scoring tool, with a baseline from the earlier version ([b9bf928](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/b9bf928593e33ebc1111a568d5a74a353d083605))
* **golden:** run the golden paper through the new API ([8124f4e](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/8124f4efcd7d58d10f09862240fc8cff78aaea59))
* keep predictions cut off at the model's token limit and flag them as incomplete ([34a5ceb](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/34a5cebbea812f4e29f6631c15c0ebbdcde77fbf))
* **ocsr:** lower the ocsr memory limit to 4 GB ([5e9607a](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/5e9607a941917dd341f7e1167a93d944e25b63ac))
* **ocsr:** structure recognition service ([77d0d4c](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/77d0d4cf9ba64a700f71be75f5df2df497cf9180))
* privacy text, problem reports expire after 364 days, edge error log kept for 7 days ([14053c8](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/14053c8186340b77bd33312cd8ec81adf900172e))
* project foundations and the Ketcher editor prototype ([bdbfec2](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/bdbfec2eeeb529320e9ca61c3d09219cbd7a418e))
* **seg:** segmentation service; readiness counts successful inferences ([796b8da](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/796b8da622053c8d20f2abe828df1d2f351b1bad))
* **services:** shared HTTP skeleton for the model services ([ee32387](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/ee323871ccac04e3d1cb48c4fffe311be03273b1))
* **web:** About page and footer redesign; logo animation plays once per browser tab ([1f59ebf](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/1f59ebfd6749b5c509b2ab5802ce7802fc64dd99))
* **web:** About, Privacy and Impressum pages ([fb55310](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/fb5531001552f53c2e4bfad049fc1280ac91775c))
* **web:** first user interface on a mock API ([59ccf8f](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/59ccf8f2cb409db9695c62fbdb2d4717874708fb))
* **web:** harden the real API client, check the schema for drift, show names on hover ([7ddbe40](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/7ddbe40cda05e7649e6106c84c7ca00ec77d8a03))
* **web:** home page ribbons move like heavy cloth ([d4faf07](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/d4faf07b7f6e2435b6fe722d98259cc1ead653ba))
* **web:** new visual design, part 1: colours, fonts, buttons, page shell and home page ([43f8bf0](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/43f8bf01e1eb788b7355d8e8549b9f06fcd1b86f))
* **web:** new visual design, part 2: working and failed states ([377dc03](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/377dc03fd8a1dd1a776a42643210c46e055e3ade))
* **web:** new visual design, part 3: results overview and editor ([81a5d11](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/81a5d1196a91efb20bf43a600ec6762e7489104e))
* **web:** show the incomplete flag in the interface; frame labels stay visible ([3055eef](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/3055eeff9216f28c336015f0e68a4655402ca4ab))
* **web:** show the uploaded PDF beside the results, and behaviour fixes to match the earlier version ([dbd853e](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/dbd853e79a7c678f87093677b4cf1e399b0c03d1))
* **web:** working-screen animation, document view, results and editor rework, new footer ([79e16d5](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/79e16d5f18460219f5fae6d0b2d480121e424ce8))


### Bug fixes

* **chem:** harden the protocol between the service and the naming process ([46ddfec](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/46ddfecf268fe7d3cfd0f5fd847b1a49cb2ee52b))
* code scanning alerts: exact origin check in the Matomo test; PDF-standard MD5 in a test fixture marked as not for security ([3e61a5f](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/3e61a5fe5c15c7887d1b0684fa04fe983e18dda5))
* **deploy:** pyproject patches match the current comments again ([25a8ca2](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/25a8ca2e3fd3e28a08786ee0e4dddf1ff9d662e1))
* **deps:** protobuf 4.25.9 in the model services (CVE-2025-4565) ([7129a1a](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/7129a1a83d2267871d754ed6324b4ca949f0bc2d))
* **seg:** call the Mask R-CNN model through predict_on_batch to stop a memory leak ([125519f](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/125519f02733c91f405249b2843039f367990e37))
* **seg:** run one inference at a time per process; readiness answers while busy ([a07c7e5](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/a07c7e56b553454eff6ebee554b7666c30397d71))
* **services:** run the readiness selftest on a host that booted less than 30 seconds ago ([3e606b3](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/3e606b399420a8ad0f470936add242e925969950))
* **web:** an edit can no longer land on the wrong result; show CDK-only SMILES in the panel ([a17d3fc](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/a17d3fcbf5c14abc30efde99d90c8dda6736b832))
* **web:** count visits only on decimer.ai, never on self-hosted copies ([7e25bd0](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/7e25bd08ec443f9ac42867c0808284e2599310f1))
* **web:** design review fixes for the filter row, notices, tile footers, citation cards and favicon ([a2a68bb](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/a2a68bbe0b278f92ec76e5912a5a5a51162addf3))
* **web:** drop the size numbers from the drop zone label ([6922194](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/6922194c5067f35ecfe2b39e49f373f7bbb2422c))
* **web:** keyboard focus in the Ketcher toolbars and Shift+Tab ([1da4089](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/1da40895ba0e7cf61c6ca2f2e9e4cd6ef73d9027))
* **web:** Safari now draws the GitHub ribbon (the cloth canvas gets its own compositor layer) ([54af849](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/54af84931170427a2e317ca8894d0707c86b1d0d))


### Performance

* **api:** overlap the stages of a job, so pages and structures move on as soon as they are ready ([0d49ecd](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/0d49ecda8339ddb34e25c343f1aa78867abab520))


### Documentation

* **api:** rewrite comments and the README for public readers ([53460e6](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/53460e687ff93e8a511c5125598ccb63fdcbbbc9))
* **deploy:** self-hosting guide, generic paths, golden smoke check moved to golden/ ([d173160](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/d1731605ce781c33bf1f6dd0fdfbebfbbc6e6430))
* explain how to set up a fresh checkout ([b1e2aae](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/b1e2aae420dbed96a9080bec1c2bfdc3040ad412))
* neutral wording in tests and correct section references ([1ca610e](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/1ca610ec9dd8ce7af3e114ccd70f4839341abf13))
* README images in .github/readme and a recorded golden run ([e9114fc](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/e9114fc667b1714d1cf6cd3de49a263306cdb1c8))
* redesign the README with a banner, pipeline diagram, screenshots and citation ([83c7f2b](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/83c7f2baaeb41c223e37a69ada7cc9c72e91ce00))
* **services:** rewrite comments and READMEs for public readers ([eae8989](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/eae898989fe0b7043e97ae3dcb2a4aa7f12f8b06))
* **web:** finalise the About, Privacy and Impressum statements ([3c78458](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/3c78458626c1246d99b92c973d59af8ec0b61b20))
* **web:** update comments after the redesign ([bd99352](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/bd99352a66644bfcdf3a8e6b9e08aa094055808b))


### CI

* add release-please, Dependabot version updates and a conventional PR title check ([275e11a](https://github.com/Steinbeck-Lab/DECIMER.ai/commit/275e11a451a639b28de22349af1846b53ae01f18))
