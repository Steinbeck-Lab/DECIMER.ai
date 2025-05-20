<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="ie-edge">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
    <meta http-equiv="Pragma" content="no-cache"/>
    <meta http-equiv="Expires" content="0"/>
    <link rel="stylesheet" href="{{ asset('css/app.css') }}">
    <link rel="stylesheet" href="{{ asset('css/tailwind.min.css') }}">
    <link rel="stylesheet" href="{{ asset('css/bootstrap.min.css') }}">
    <script src="/js/app.js?1238"></script>
    <script src="/js/jquery.min.js"></script>
    <link rel="icon" href=" {{ asset('DECIMER_favicon.png') }}">
    <title>DECIMER Web Application</title>
    <!--Global site tag (gtag.js) - Google Analytics-->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-VKSWMKC79R"></script>
    <script>
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());

        gtag('config', 'G-VKSWMKC79R');
    </script>
    <script>
        var _paq = window._paq = window._paq || [];
        /* tracker methods like "setCustomDimension" should be called before "trackPageView" */
        _paq.push(['trackPageView']);
        _paq.push(['enableLinkTracking']);
        (function() {
            var u="//matomo.nfdi4chem.de/";
            _paq.push(['setTrackerUrl', u+'matomo.php']);
            _paq.push(['setSiteId', '2']);
            var d=document, g=d.createElement('script'), s=d.getElementsByTagName('script')[0];
            g.async=true; g.src=u+'matomo.js'; s.parentNode.insertBefore(g,s);
        })();
    </script>

    <!-- Add some additional styling -->
    <style>
        .btn-primary {
            @apply bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 transition;
        }
        .btn-secondary {
            @apply bg-gray-200 text-gray-800 px-4 py-2 rounded-md hover:bg-gray-300 transition;
        }
        .btn-dark {
            @apply bg-gray-800 text-white px-4 py-2 rounded-md hover:bg-gray-700 transition;
        }

        /* Animation utility classes */
        .animate-pulse {
            animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .animate-float {
            animation: float 6s ease-in-out infinite;
        }
        @keyframes float {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
            100% { transform: translateY(0px); }
        }

        .animate-spin-slow {
            animation: spin 20s linear infinite;
        }
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }

        .animate-gradient {
            background-size: 200% 200%;
            animation: gradient 15s ease infinite;
        }
        @keyframes gradient {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }
    </style>
</head>

<body class="bg-white flex flex-col min-h-screen">

    <header class="bg-gradient-to-r from-white to-blue-50 shadow-sm sticky top-0 z-50">
        <div class="container mx-auto flex justify-between items-center py-3 px-4">
            <!-- Navigation Links on the left -->
            <nav class="flex items-center space-x-1 sm:space-x-4">
                <!-- Navigation Links -->
                <div class="flex items-center space-x-1 sm:space-x-3">
                    <a href="{{ route('home') }}" class="text-gray-700 hover:text-blue-600 transition px-2 py-1 rounded-md hover:bg-blue-50">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 sm:hidden" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" />
                        </svg>
                        <span class="hidden sm:inline">Home</span>
                    </a>
                    <a href="{{ url('/about') }}" class="text-gray-700 hover:text-blue-600 transition px-2 py-1 rounded-md hover:bg-blue-50">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 sm:hidden" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" />
                        </svg>
                        <span class="hidden sm:inline">About</span>
                    </a>
                    <a href="https://github.com/Steinbeck-Lab/DECIMER.ai" target="_blank" class="text-gray-700 hover:text-blue-600 transition px-2 py-1 rounded-md hover:bg-blue-50 flex items-center">
                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
                        </svg>
                        <span class="hidden sm:inline ml-1">GitHub</span>
                    </a>
                </div>
            </nav>

            <div class="flex items-center absolute left-1/2 transform -translate-x-1/2">
                <div class="flex items-center bg-gray-100 rounded-full px-3 py-1 shadow-inner" id="status_container" style="display: none;">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 text-blue-500 animate-spin mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                        <path d="M12 6v6l4 2"></path>
                    </svg>
                    <div class="text-sm font-medium text-gray-700" id="loading_text"></div>
                </div>
            </div>

            <div class="flex items-center">
                @if (Session::get('smiles_array'))
                    <div class="flex space-x-2 mr-2">
                    <!-- HEADER IUPAC GENERATION BUTTON -->
                    <form id="iupac_generation_form" action="{{ route('stout.iupac.post') }}" method="POST" enctype="multipart/form-data">
                            @csrf
                            <input type="hidden" name="img_paths" value="{{ Session::get('img_paths') }}" />
                            <input type="hidden" name="structure_depiction_img_paths" value="{{ Session::get('structure_depiction_img_paths') }}" />
                            <input type="hidden" name="iupac_array" value="{{ Session::get("iupac_array") }}" />
                            <input type="hidden" id="smiles_array" name="smiles_array" value="{{ Session::get('smiles_array') }}" />
                            <input type="hidden" id="stout_form_molfile_array" name="mol_file_array" />
                            <input type="hidden" id="classifier_result_array" name="classifier_result_array" value="{{ Session::get('classifier_result_array') }}" />
                            <input type="hidden" id="stout_form_has_segmentation_already_run" name="has_segmentation_already_run" />
                            <input type="hidden" id="stout_form_single_image_upload" name="single_image_upload" />
                            <?php
                                $num_ketcher_frames = count(json_decode(Session::get('smiles_array')));
                                if ($num_ketcher_frames > 20) {
                                    $num_ketcher_frames = 20;
                                }
                            ?>
                            <button class="inline-flex items-center h-9 text-sm bg-gray-700 bg-opacity-90 text-white px-3 rounded-md hover:bg-opacity-100 transition"
        onclick="stout_submit('{{ $num_ketcher_frames }}', 'stout_form_molfile_array')">
    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
    </svg>
    Generate IUPAC
</button>
                        </form>

                        <!-- HEADER DOWNLOAD BUTTON -->
                        <form id="archive_creation_form" action="{{ route('archive.creation.post') }}" method="POST" enctype="multipart/form-data">
                            @csrf
                            <input type="hidden" name="img_paths" value="{{ Session::get('img_paths') }}" />
                            <input type="hidden" name="structure_depiction_img_paths" value="{{ Session::get('structure_depiction_img_paths') }}" />
                            <input type="hidden" name="iupac_array" value="{{ Session::get("iupac_array") }}" />
                            <input type="hidden" id="smiles_array" name="smiles_array" value="{{ Session::get('smiles_array') }}" />
                            <input type="hidden" id="header_download_form_molfile_array" name="mol_file_array" />
                            <input type="hidden" id="classifier_result_array" name="classifier_result_array" value="{{ Session::get('classifier_result_array') }}" />
                            <input type="hidden" id="header_download_form_has_segmentation_already_run" name="has_segmentation_already_run" />
                            <input type="hidden" id="header_download_form_single_image_upload" name="single_image_upload" />
                            <?php
                                $num_ketcher_frames = count(json_decode(Session::get('smiles_array')));
                                if ($num_ketcher_frames > 20) {
                                    $num_ketcher_frames = 20;
                                }
                            ?>
                            <button class="inline-flex items-center h-9 text-sm bg-gray-200 bg-opacity-90 text-gray-700 px-3 rounded-md hover:bg-opacity-100 transition"
        onclick="submit_with_updated_molfiles('{{ $num_ketcher_frames }}', 'header_download_form_molfile_array')">
    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
    </svg>
    Download
</button>
                        </form>
                    </div>
                @endif
                <img src="loading_icon_mini.gif" alt="Loading icon" class="mx-2" id="header_loading_icon" style="display: none; visibility: hidden;"/>
                <div class="text-lg text-gray-800 mx-2" id="loading_text" style="display: inline;"></div>
            </div>
        </div>
    </header>

    <main class="flex-grow py-4">
        @yield('page-content')

        <!-- Processing sections for DECIMER -->
        @if (isset($img_paths) || isset($structure_depiction_img_paths) || isset($smiles_array))
            <div id="processing-results">
                <!-- This will contain all the processing results that are currently in the index.blade.php -->
            </div>
        @endif
    </main>

    <footer class="bg-gradient-to-r from-blue-50 to-indigo-50 border-t border-blue-100 mt-12">
        <div class="max-w-screen-lg mx-auto py-4 px-6">
            <!-- Development Credit and Contact on one line -->
            <div class="text-center text-xs text-gray-600 mb-3">
                DECIMER (Deep lEarning for Chemical IMagE Recognition) is developed with
                <svg xmlns="http://www.w3.org/2000/svg" class="inline-block h-4 w-4 text-yellow-700 animate-float" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 8h1a4 4 0 010 8h-1M2 8h16v9a4 4 0 01-4 4H6a4 4 0 01-4-4V8zm4-7v3M12 1v3M8 1v3" />
                </svg>
                by the <a href="https://cheminf.uni-jena.de/" class="text-indigo-600 hover:text-indigo-800 font-semibold transition">Steinbeck group</a> at
                <a href="https://www.uni-jena.de/" class="text-indigo-600 hover:text-indigo-800 font-semibold transition">Friedrich Schiller University Jena</a>.
                You can contact us directly via <a href="mailto:kohulan.rajan@uni-jena.de;christoph.steinbeck@uni-jena.de" class="text-indigo-600 hover:text-indigo-800 transition">email</a> or
                create an <a href="https://github.com/Steinbeck-Lab/DECIMER.ai/issues" class="text-indigo-600 hover:text-indigo-800 transition">issue on GitHub</a>.
            </div>

            <!-- Copyright -->
            <div class="pt-3 border-t border-blue-100">
                <div class="flex flex-col items-center">
                    <p class="text-xs text-gray-500 mb-2">
                        &copy; {{ date('Y') }} <span class="font-semibold">DECIMER Project</span>. All rights reserved.
                    </p>

                    <!-- Legal links below copyright -->
                    <div class="flex space-x-4 text-xs text-gray-400">
                        <a href="{{ route('privacy_policy') }}" class="hover:text-indigo-500 transition">Privacy Policy</a>
                        <span>|</span>
                        <a href="{{ route('impressum') }}" class="hover:text-indigo-500 transition">Impressum</a>
                    </div>
                </div>
            </div>
        </div>
    </footer>



</body>

</html>