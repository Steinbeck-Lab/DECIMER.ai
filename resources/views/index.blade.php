@extends('layouts.default')

@section('page-content')
    <!-- Set env variables to make sure DECIMER Segmentation runs on single uploaded image -->
    @if ($img_paths = Session::get('img_paths'))
        @if ($structure_depiction_img_paths = Session::get('structure_depiction_img_paths'))
            <?php
            $structure_img_paths_array = json_decode($structure_depiction_img_paths);
            $structure_img_paths_array = is_array($structure_img_paths_array) ? $structure_img_paths_array : [];
            ?>
            <?php
            $has_segmentation_already_run = Session::get("has_segmentation_already_run");
            ?>
            <?php
            $single_image_upload = Session::get("single_image_upload");
            ?>
            @if ($has_segmentation_already_run != 'true')
                @if (count($structure_img_paths_array) == 1)
                    <?php
                    $single_image_upload = "true";
                    ?>
                @endif
            @endif
        @endif
    @endif

    <!-- JavaScript Functions -->
    <script>
        // Load molecular structure into Ketcher iframe
        function loadMol(molBlock, frameId) {
            try {
                const iframe = document.getElementById(frameId);
                if (!iframe) {
                    console.error('Iframe not found:', frameId);
                    return;
                }
                
                // Wait for iframe to load completely
                iframe.onload = function() {
                    try {
                        if (iframe.contentWindow && iframe.contentWindow.ketcher) {
                            const parsedMol = JSON.parse(molBlock);
                            if (parsedMol && parsedMol !== 'invalid') {
                                iframe.contentWindow.ketcher.setMolecule(parsedMol);
                            }
                        }
                    } catch (e) {
                        console.error('Error loading molecule into Ketcher:', e);
                    }
                };
            } catch (e) {
                console.error('Error in loadMol:', e);
            }
        }

        // Toggle display of elements
        function display_or_not(checkbox_id, element_id) {
            const checkbox = document.getElementById(checkbox_id);
            const element = document.getElementById(element_id);
            if (checkbox && element) {
                element.style.display = checkbox.checked ? 'block' : 'none';
            }
        }

        // Get mol files from all Ketcher frames and submit form
        function submit_with_updated_molfiles(num_frames, target_input_id) {
            event.preventDefault();
            
            const mol_blocks = [];
            for (let i = 0; i < num_frames; i++) {
                const frameId = (i * 2 + 1).toString();
                const iframe = document.getElementById(frameId);
                
                try {
                    if (iframe && iframe.contentWindow && iframe.contentWindow.ketcher) {
                        const molBlock = iframe.contentWindow.ketcher.getMolfile();
                        mol_blocks.push(molBlock);
                    } else {
                        mol_blocks.push('');
                    }
                } catch (e) {
                    console.error('Error getting mol file from frame:', frameId, e);
                    mol_blocks.push('');
                }
            }
            
            // Set the mol blocks in the hidden input
            const targetInput = document.getElementById(target_input_id);
            if (targetInput) {
                targetInput.value = JSON.stringify(mol_blocks);
            }
            
            // Also update other hidden fields
            const hasSegmentation = document.getElementById('download_form_has_segmentation_already_run');
            const singleImage = document.getElementById('download_form_single_image_upload');
            
            if (hasSegmentation) {
                const sourceValue = document.getElementById('has_segmentation_already_run');
                if (sourceValue) hasSegmentation.value = sourceValue.value;
            }
            
            if (singleImage) {
                const sourceValue = document.getElementById('single_image_upload');
                if (sourceValue) singleImage.value = sourceValue.value;
            }
            
            // Submit the form
            event.target.form.submit();
        }

        // Handle problem report submission
        function handle_problem_report(key) {
            event.preventDefault();
            const form = document.getElementById('problem_report_form_' + key);
            if (form) {
                form.submit();
                alert('Thank you for reporting this problem. We will review it.');
            }
            return false;
        }

        // Download file from URL
        function downloadURI(uri, name) {
            const link = document.createElement('a');
            link.download = name;
            link.href = uri;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    </script>

    <section class="max-w-screen-lg container mx-auto flex-grow">
        <div class="py-8">
            <!-- Logos with Google-inspired styling -->
            <div class="flex flex-col items-center">
                <img src="DECIMER.gif" alt="DECIMER GIF Logo" id="decimer_logo_gif"
                    style="display: none; margin: 0 auto; max-width: 450px;" />
                <img src="DECIMER_Clean.png" alt="DECIMER Logo" id="decimer_logo"
                    style="display: none; margin: 0 auto; max-width: 450px;" />
            </div>
            <img src="loading_icon.gif" alt="Loading icon" class="mx-auto" id="loading_icon" style="display: none;" />

            <!-- DECIMER LOGO (Animated gif is only shown the first time we are sent to index view) -->
            @if (!Session::get('img_paths'))
                <script>
                    // Check if this is the first visit using localStorage
                    if (!localStorage.getItem('visited')) {
                        document.getElementById("decimer_logo_gif").style = "display: block; margin: 0 auto; max-width: 450px;";
                        localStorage.setItem('visited', 'true');
                    } else {
                        document.getElementById("decimer_logo").style = "display: block; margin: 0 auto; max-width: 450px;";
                    }
                </script>

                <!-- UPLOAD BUTTON with Google-style -->
                <div class="container flex justify-center mt-8 mb-16">
                    <div class="w-full max-w-xl">
                        <div id="upload-area"
                            class="mx-auto bg-white border border-gray-200 shadow-md hover:shadow-lg rounded-full py-4 px-6 cursor-pointer transition relative flex flex-col items-center justify-center">
                            <div class="space-y-2 pointer-events-none">
                                <div class="text-center">
                                    <span class="block text-gray-500 text-sm font-light">
                                        Drop files, click to browse, or paste (Ctrl+V)
                                    </span>
                                </div>

                                <form id="upload_form" action="{{ route('file.upload.post') }}" method="POST"
                                    enctype="multipart/form-data">
                                    @csrf
                                    <input class="file-input hidden" type="file" name="file[]" multiple>
                                </form>

                                <form id="paste_form" action="{{ route('clipboard.paste.post') }}" method="POST"
                                    enctype="multipart/form-data">
                                    @csrf
                                    <input type="hidden" name="clipboard_image" id="clipboard_image_input">
                                </form>
                            </div>

                            <div id="preview-area" class="hidden mt-4 max-w-full">
                                <img id="preview-image" class="max-h-48 mx-auto rounded" alt="Preview">
                            </div>
                        </div>
                    </div>
                </div>

                <script>
                    document.addEventListener('DOMContentLoaded', function () {
                        const uploadArea = document.getElementById('upload-area');
                        const fileInput = document.querySelector('.file-input');
                        const previewArea = document.getElementById('preview-area');
                        const previewImage = document.getElementById('preview-image');

                        uploadArea.addEventListener('click', function (e) {
                            if (e.target === uploadArea || e.target.parentNode === uploadArea) {
                                fileInput.click();
                            }
                        });

                        fileInput.addEventListener('change', function () {
                            document.getElementById("loading_icon").style = "display: centered;";
                            const headerIcon = document.getElementById("header_loading_icon");
                            const loadingText = document.getElementById("loading_text");
                            if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                            if (loadingText) loadingText.innerHTML = "Uploading files...";
                            document.getElementById('upload_form').submit();
                        });

                        document.addEventListener('paste', function (e) {
                            e.preventDefault();
                            const items = e.clipboardData.items;

                            for (const item of items) {
                                if (item.type.indexOf('image') !== -1) {
                                    const blob = item.getAsFile();
                                    const reader = new FileReader();

                                    reader.onload = function (e) {
                                        previewArea.classList.remove('hidden');
                                        previewImage.src = e.target.result;
                                        document.getElementById('clipboard_image_input').value = e.target.result;

                                        setTimeout(() => {
                                            document.getElementById("loading_icon").style = "display: centered;";
                                            const headerIcon = document.getElementById("header_loading_icon");
                                            const loadingText = document.getElementById("loading_text");
                                            if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                                            if (loadingText) loadingText.innerHTML = "Processing pasted image...";
                                            document.getElementById('paste_form').submit();
                                        }, 500);
                                    };

                                    reader.readAsDataURL(blob);
                                    break;
                                }
                            }
                        });

                        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                            uploadArea.addEventListener(eventName, function(e) {
                                e.preventDefault();
                                e.stopPropagation();
                            }, false);
                        });

                        uploadArea.addEventListener('dragenter', function() {
                            uploadArea.classList.add('bg-blue-100');
                        }, false);
                        
                        uploadArea.addEventListener('dragover', function() {
                            uploadArea.classList.add('bg-blue-100');
                        }, false);
                        
                        uploadArea.addEventListener('dragleave', function() {
                            uploadArea.classList.remove('bg-blue-100');
                        }, false);
                        
                        uploadArea.addEventListener('drop', function(e) {
                            uploadArea.classList.remove('bg-blue-100');
                            const dt = e.dataTransfer;
                            const files = dt.files;
                            fileInput.files = files;
                            document.getElementById('upload_form').submit();
                        }, false);
                    });
                </script>
            @elseif (Session::get('img_paths') == '[]')
                <script>
                    document.getElementById("decimer_logo").style = "display: block; margin: 0 auto; max-width: 450px;"
                </script>
                @if (!Session::get('smiles_array'))
                    @if ($single_image_upload != 'true')
                        <script>
                            document.getElementById("loading_icon").style = "display: centered;";
                            const headerIcon = document.getElementById("header_loading_icon");
                            const loadingText = document.getElementById("loading_text");
                            if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                            if (loadingText) loadingText.innerHTML = "Interpreting structure images...";
                        </script>
                        <p style="text-align:center">
                            The uploaded images are presented below.</br>
                            The DECIMER OCSR engine is running.</br>
                            This may take a few minutes.
                        </p>
                    @else
                        <script>
                            document.getElementById("loading_icon").style = "display: centered;";
                            const headerIcon = document.getElementById("header_loading_icon");
                            const loadingText = document.getElementById("loading_text");
                            if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                            if (loadingText) loadingText.innerHTML = "Searching for chemical structures";
                        </script>
                        <p style="text-align:center">
                            The image has been uploaded. </br>
                            Detecting chemical structures.</br>
                            This may take a moment.
                        </p>
                    @endif
                @endif
            @else
                <script>
                    document.getElementById("decimer_logo").style = "display: block; margin: 0 auto; max-width: 450px;"
                </script>
                @if (!Session::get('structure_depiction_img_paths'))
                    <script>
                        document.getElementById("loading_icon").style = "display: centered;";
                        const headerIcon = document.getElementById("header_loading_icon");
                        const loadingText = document.getElementById("loading_text");
                        if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                        if (loadingText) loadingText.innerHTML = "Searching for chemical structures...";
                    </script>
                    <p style="text-align:center">
                        The document has been uploaded and converted.</br>
                        Detecting chemical structures.</br>
                        This may take a few minutes.
                    </p>
                @elseif (!Session::get('smiles_array'))
                    @if (Session::get('structure_depiction_img_paths') == '[]')
                        @if ($single_image_upload != 'true')
                            <p style="text-align:center">
                                No structures were detected in the uploaded document.
                            </p>
                        @else
                            <script>
                                document.getElementById("loading_icon").style = "display: centered;";
                                const headerIcon = document.getElementById("header_loading_icon");
                                const loadingText = document.getElementById("loading_text");
                                if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                                if (loadingText) loadingText.innerHTML = "Interpreting uploaded image...";
                            </script>
                            <p style="text-align:center">
                                The DECIMER OCSR engine is running on the uploaded image.</br>
                                This may take a moment.
                            </p>
                        @endif
                    @else
                        <script>
                            document.getElementById("loading_icon").style = "display: centered;";
                            const headerIcon = document.getElementById("header_loading_icon");
                            const loadingText = document.getElementById("loading_text");
                            if (headerIcon) headerIcon.style = "display: block; visibility: visible;";
                            if (loadingText) loadingText.innerHTML = "Interpreting structure images...";
                        </script>
                        <p style="text-align:center">
                            The segmented chemical structures are presented below.</br>
                            The DECIMER OCSR engine is running.</br>
                            This may take a few minutes.
                        </p>
                    @endif
                @endif
            @endif
            @if (Session::get('smiles_array'))
                <p style="text-align:center" id="main_loading_text">
                    The chemical structure depictions have been processed.</br>
                    The results are presented below.</br></br></br>
                </p>
                <span id="smiles_error_span"></span>
                <form id="archive_creation_form" action="{{ route('archive.creation.post') }}" method="POST"
                    enctype="multipart/form-data">
                    @csrf
                    <div class="container d-flex justify-content-center">
                        <div class="row">
                            <div class="col-md-12">
                                <div class="mx-auto bg-gray-300 text-center p-4 rounded hover:bg-blue-100 transition">
                                    <span>Download results</span>
                                    <input type="hidden" name="img_paths" value="{{ Session::get('img_paths') }}" />
                                    <input type="hidden" name="structure_depiction_img_paths"
                                        value="{{ Session::get('structure_depiction_img_paths') }}" />
                                    <input type="hidden" name="iupac_array" value="{{ Session::get('iupac_array') }}" />
                                    <input type="hidden" id="smiles_array" name="smiles_array"
                                        value="{{ Session::get('smiles_array') }}" />
                                    <input type="hidden" id="download_form_molfile_array" name="mol_file_array" />
                                    <input type="hidden" id="classifier_result_array" name="classifier_result_array"
                                        value="{{ Session::get('classifier_result_array') }}" />
                                    <input type="hidden" id="download_form_has_segmentation_already_run"
                                        name="has_segmentation_already_run" />
                                    <input type="hidden" id="download_form_single_image_upload" name="single_image_upload" />
                                    <?php
                                    $smiles_session = Session::get("smiles_array");
                                    $smiles_decoded = null;
                                    $num_ketcher_frames = 0;
                                    
                                    if ($smiles_session) {
                                        $smiles_decoded = json_decode($smiles_session);
                                        if (is_array($smiles_decoded) || is_countable($smiles_decoded)) {
                                            $num_ketcher_frames = count($smiles_decoded);
                                        }
                                    }
                                    
                                    if ($num_ketcher_frames > 20) {
                                        $num_ketcher_frames = 20;
                                    }
                                    ?>
                                    <button class="file-input"
                                        onclick="submit_with_updated_molfiles('{{ $num_ketcher_frames }}', 'download_form_molfile_array')">
                                </div>
                            </div>
                        </div>
                    </div>
                </form>
                @if ($download_link = Session::get('download_link'))
                    <script async type="module">
                        downloadURI("{{ $download_link }}", "{{ basename($download_link) }}");
                    </script>
                @endif
            @endif
            </br>
            <div role="alert" id='alert-if-safari'></div>
            <script>
                var is_safari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
                if (is_safari) {
                    const alert_div = document.getElementById('alert-if-safari')
                    alert_div.className = 'alert alert-danger'
                    alert_div.innerHTML =
                        'We have some problems with Safari! While the app is fully functional, the loading icons do not move while your data is processed. This may appear like the website is frozen. Please use a different browser to get the best user experience!'
                }
            </script>
            </br></br></br>
        </div>

        <?php $single_image_upload = Session::get("single_image_upload"); ?>
        @if ($img_paths = Session::get('img_paths'))
            @if ($img_paths != '[]')
                @if ($img_paths != $structure_depiction_img_paths)
                    <div class="text-xl mb-3 gray-800">
                        Display uploaded document
                        <input type="checkbox" id="page_image_checkbox" onclick="display_or_not('page_image_checkbox', 'page_images')">
                    </div>
                @endif
                <?php
                $img_paths_array = json_decode($img_paths);
                $img_paths_array = is_array($img_paths_array) ? $img_paths_array : [];
                ?>
                @if (count($img_paths_array) == 10)
                    <div class="text-xl mb-3 text-red-800">
                        <strong>Warning:</strong> If you upload a pdf document with more than 10 pages,
                        only the first 10 pages are processed. Please host your own version of this
                        application if you want to process large amounts of data.
                    </div>
                @endif
                <div id="page_images" class="flex overflow-auto max-h-screen" style="display:none">
                    @foreach ($img_paths_array as $img_path)
                        <img src="{{ asset($img_path) }}" alt="page image" class="w-7/12">
                    @endforeach
                </div>
            @endif

            @if ($structure_depiction_img_paths = Session::get('structure_depiction_img_paths'))
                <?php
                $structure_img_paths_array = json_decode($structure_depiction_img_paths);
                $structure_img_paths_array = is_array($structure_img_paths_array) ? $structure_img_paths_array : [];
                ?>
                <?php
                $has_segmentation_already_run = Session::get("has_segmentation_already_run");
                ?>
                <?php
                $single_image_upload = Session::get("single_image_upload");
                ?>
                @if ($has_segmentation_already_run != 'true')
                    @if (count($structure_img_paths_array) == 1)
                        <?php
                        $img_paths = $structure_depiction_img_paths;
                        ?>
                        <?php
                        $structure_depiction_img_paths = null;
                        ?>
                        <?php
                        $single_image_upload = "true";
                        ?>
                    @endif
                @endif

                @if (count($structure_img_paths_array) > 20)
                    <div class="text-xl mb-3 text-red-800">
                        <strong>Warning:</strong> It appears like you uploaded more than 20 chemical
                        structure depictions (or we detected more than 20 structures in your uploaded
                        document). Only the first 20 structures are processed. Please host your own
                        version of this application if you want to process a large amounts of data.
                    </div>
                @endif
                @if ($smiles_array_str = Session::get('smiles_array'))
                    <?php
                    $smiles_array = json_decode($smiles_array_str);
                    $smiles_array = is_array($smiles_array) ? $smiles_array : [];
                    ?>
                @endif
                @if ($iupac_array_str = Session::get('iupac_array'))
                    <?php
                    $iupac_array = json_decode($iupac_array_str);
                    $iupac_array = is_array($iupac_array) ? $iupac_array : [];
                    ?>
                @endif
                @if ($validity_array_str = Session::get('validity_array'))
                    <?php
                    $validity_array = json_decode($validity_array_str);
                    $validity_array = is_array($validity_array) ? $validity_array : [];
                    ?>
                @endif
                @if ($inchikey_array_str = Session::get('inchikey_array'))
                    <?php
                    $inchikey_array = json_decode($inchikey_array_str);
                    $inchikey_array = is_array($inchikey_array) ? $inchikey_array : [];
                    ?>
                @endif
                @if ($classifier_result_array_str = Session::get('classifier_result_array'))
                    <?php
                    $classifier_result_array = json_decode($classifier_result_array_str);
                    $classifier_result_array = is_array($classifier_result_array) ? $classifier_result_array : [];
                    ?>
                @endif

                <div class="grid grid-cols-3 gap-4">
                    @foreach ($structure_img_paths_array as $key => $struc_img_path)
                        <div class="col-span-3 border-t">
                            @if ($key < 20)
                                @if (Session::get('smiles_array'))
                                    <?php
                                    $classifier_result = $classifier_result_array[$key] ?? 'True';
                                    $current_smiles = $smiles_array[$key] ?? '';
                                    $current_validity = $validity_array[$key] ?? 'invalid';
                                    $current_inchikey = $inchikey_array[$key] ?? 'invalid';
                                    ?>
                                    
                                    @if ($classifier_result == 'False')
                                        <div class="text-red-800">
                                            <strong>We are not sure if this is a chemical structure.</strong></br>
                                            Our system has come to the conclusion that this
                                            image might not be a chemical structure depiction. </br>
                                            A SMILES string has been generated anyway.
                                        </div>
                                    @endif

                                    <strong>Resolved SMILES representation</strong></br>
                                    <a class="break-words"> {{ $current_smiles }} </a>
                                    
                                    @if ($current_validity != 'invalid')
                                        <?php
                                        $has_stereo = false;
                                        if (is_string($current_inchikey) && strlen($current_inchikey) === 27) {
                                            $has_stereo = substr($current_inchikey, 14, 1) !== "A";
                                        }
                                        ?>
                                        <a> - </a>
                                        <span class="text-blue-400">
                                            Search on PubChem:
                                            @if ($has_stereo)
                                                <span class="relative inline-block w-10 mr-2 align-middle select-none transition duration-200 ease-in">
                                                    <input type="checkbox" name="toggle" id="toggle-{{ $key }}"
                                                        class="toggle-checkbox absolute block w-6 h-6 rounded-full bg-white border-4 appearance-none cursor-pointer" />
                                                    <label for="toggle-{{ $key }}"
                                                        class="toggle-label block overflow-hidden h-6 rounded-full bg-gray-300 cursor-pointer"></label>
                                                </span>
                                                <span id="search-type-{{ $key }}">with stereo</span>
                                            @endif
                                            <a id="pubchem-link-{{ $key }}"
                                                href="https://pubchem.ncbi.nlm.nih.gov/#query={{ $current_inchikey }}" target="_blank"
                                                class="hover:text-blue-600 transition">
                                                Search
                                            </a>
                                        </span>
                                        <br>
                                        <a href="https://coconut.naturalproducts.net/search?q={{ $current_inchikey }}" target="_blank"
                                            class="text-green-500 hover:text-green-800 transition">
                                            Search for this structure on COCONUT
                                        </a>

                                        @if ($has_stereo)
                                            <?php
                                            $inchikey_no_stereo = substr($current_inchikey, 0, 14);
                                            ?>
                                            <script>
                                                document.getElementById('toggle-{{ $key }}').addEventListener('change', function () {
                                                    var searchType = document.getElementById('search-type-{{ $key }}');
                                                    var pubchemLink = document.getElementById('pubchem-link-{{ $key }}');
                                                    if (this.checked) {
                                                        searchType.textContent = 'without stereo';
                                                        pubchemLink.href = 'https://pubchem.ncbi.nlm.nih.gov/#query={{ $inchikey_no_stereo }}';
                                                    } else {
                                                        searchType.textContent = 'with stereo';
                                                        pubchemLink.href = 'https://pubchem.ncbi.nlm.nih.gov/#query={{ $current_inchikey }}';
                                                    }
                                                });
                                            </script>

                                            <style>
                                                .toggle-checkbox:checked {
                                                    right: 0;
                                                    border-color: #68D391;
                                                }
                                                .toggle-checkbox:checked+.toggle-label {
                                                    background-color: #68D391;
                                                }
                                            </style>
                                        @endif
                                    @endif
                                    </br>
                                @endif
                                @if (Session::get('iupac_array'))
                                    <?php
                                    $current_iupac = $iupac_array[$key] ?? '';
                                    ?>
                                    <strong>IUPAC name</strong> </br>
                                    <a class="break-words"> {{ $current_iupac }} </a></br>
                                @endif
                            @endif
                        </div>
                        <div class="frame">
                            <img src="{{ URL::asset($struc_img_path) }}" alt="extracted structure depiction"
                                class="chemical_structure_img">
                            @if (Session::get('smiles_array'))
                                @if ($key < 20)
                                    <?php
                                    $current_validity = $validity_array[$key] ?? 'invalid';
                                    $current_smiles = $smiles_array[$key] ?? '';
                                    ?>
                                    @if ($current_validity == 'invalid')
                                        <div class="text-red-800">
                                            <strong>Warning:</strong> SMILES is invalid or contains R groups and may not be depicted correctly in
                                            the editor window!
                                        </div>
                                    @endif
                                    <iframe name="dummyframe" id="dummyframe" style="display: none;"></iframe>
                                    <form id="problem_report_form_{{ $key }}" target="dummyframe" action="{{ route('problem.report.post') }}"
                                        method="POST">
                                        @csrf
                                        <input type="hidden" name="structure_depiction_img_path" value="{{ $struc_img_path }}" />
                                        <input type="hidden" name="smiles" value="{{ $current_smiles }}" />
                                    </form>
                                    <a href="" target="_blank" id="problem_report_link_{{ $key }}"
                                        class="text-blue-400 hover:text-blue-600 transition absolute bottom-0"
                                        onclick="handle_problem_report({{ $key }})">
                                        Report a problem with this result
                                    </a>
                                @else
                                    <strong>The image has not been processed.</strong> </br>
                                @endif
                            @endif
                        </div>
                        <div class="col-span-2">
                            @if ($smiles_array_str = Session::get('smiles_array'))
                                @if ($key < 20)
                                    <?php
                                    $current_validity = $validity_array[$key] ?? 'invalid';
                                    $validity_json = json_encode(str_replace('\\', '\\\\', $current_validity));
                                    ?>
                                    <iframe id='{{ $key * 2 + 1 }}' name='{{ $key * 2 + 1 }}' src="ketcher_standalone/index.html" width="100%"
                                        height="420px"
                                        onload="loadMol({{ $validity_json }}, '{{ $key * 2 + 1 }}')">
                                    </iframe>
                                @else
                                    <div class="text-xl mb-3 text-red-800">
                                        <strong>Warning:</strong> It appears like you uploaded more than 20 chemical
                                        structure depictions (or we detected more than 20 structures in your uploaded
                                        document). Only the first 20 structures are processed. Please host your own
                                        version of this application if you want to process a large amounts of data.
                                    </div>
                                @endif
                            @endif
                        </div>
                    @endforeach
                </div>

                @if ($single_image_upload == 'true')
                    @if ($structure_depiction_img_paths == '[]')
                        <?php
                        $structure_depiction_img_paths = $img_paths;
                        ?>
                    @endif
                @endif

                @if (!Session::get('smiles_array'))
                    @if ($structure_depiction_img_paths != '[]')
                        @if ($structure_depiction_img_paths)
                            <form id="OCSR_form" action="{{ route('decimer.ocsr.post') }}" method="POST">
                                @csrf
                                <input type="hidden" name="img_paths" value="{{ $img_paths }}" />
                                <input type="hidden" name="structure_depiction_img_paths" value="{{ $structure_depiction_img_paths }}" />
                                <input type="hidden" name="has_segmentation_already_run" value="{{ $has_segmentation_already_run }}" />
                                <input type="hidden" name="single_image_upload" value="{{ $single_image_upload }}" />
                            </form>
                            <script async type="module">
                                function send_ocsr_form() {
                                    document.getElementById('OCSR_form').submit();
                                }
                                setTimeout(send_ocsr_form, 200);
                            </script>
                        @endif
                    @endif
                @endif
            @endif
            @if (!$structure_depiction_img_paths)
                <form id="segmentation_form" action="{{ route('decimer.segmentation.post') }}" method="POST">
                    @csrf
                    <input type="hidden" name="img_paths" value="{{ $img_paths }}" />
                    <input type="hidden" name="single_image_upload" value="{{ $single_image_upload }}" />
                </form>
                <script async type="module">
                    function send_segmentation_form() {
                        document.getElementById('segmentation_form').submit();
                    }
                    setTimeout(send_segmentation_form, 200);
                </script>
            @endif
        @else
            @if (count($errors) > 0)
                <div class="alert alert-danger container mx-auto flex justify-between py-24">
                    <ul>
                        @foreach ($errors as $error)
                            <li><strong>{{ $error }}</strong></li>
                        @endforeach
                    </ul>
                </div>
            @endif
        @endif
        <script>
            // Null-safe value setter function
            function safeSetValue(elementId, value) {
                const element = document.getElementById(elementId);
                if (element) {
                    element.value = value || '';
                }
            }
            
            const hasSegmentation = "{{ $has_segmentation_already_run ?? '' }}";
            const singleImageUpload = "{{ $single_image_upload ?? '' }}";
            
            safeSetValue('download_form_has_segmentation_already_run', hasSegmentation);
            safeSetValue('download_form_single_image_upload', singleImageUpload);
        </script>
    </section>
@endsection