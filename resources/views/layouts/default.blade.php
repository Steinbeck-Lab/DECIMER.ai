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
    <header class="bg-white shadow-sm">
        <div class="container mx-auto flex justify-between items-center p-3">
            <div class="flex items-center">
                <a href="{{ route('home') }}" class="flex items-center">
                    <img src="{{ asset('DECIMER_Clean.png') }}" alt="DECIMER" class="h-8 mr-2">
                </a>
            </div>
            
            <div class="flex items-center">
                <img src="loading_icon_mini.gif" alt="Loading icon" class="mx-2" id="header_loading_icon" style="display: none; visibility: hidden;"/>
                <div class="text-lg text-gray-800 mx-2" id="loading_text" style="display: inline;"></div>
            </div>
            
            <nav class="flex items-center">
                @if (Session::get('smiles_array'))
                    <!-- HEADER IUPAC GENERATION BUTTON -->
                    <form id="iupac_generation_form" action="{{ route('stout.iupac.post') }}" method="POST" enctype="multipart/form-data" class="mr-2">
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
                        <button class="btn-primary" 
                                onclick="stout_submit('{{ $num_ketcher_frames }}', 'stout_form_molfile_array')">
                            Generate IUPAC names
                        </button>
                    </form>
                    <!-- HEADER DOWNLOAD BUTTON -->
                    <form id="archive_creation_form" action="{{ route('archive.creation.post') }}" method="POST" enctype="multipart/form-data" class="mr-2">
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
                        <button class="btn-secondary" 
                                onclick="submit_with_updated_molfiles('{{ $num_ketcher_frames }}', 'header_download_form_molfile_array')">
                            Download results 
                        </button>
                    </form>
                @endif
                
                <!-- Navigation Links -->
                <a href="{{ route('home') }}" class="text-gray-700 hover:text-blue-600 mx-3 transition">Home</a>
                <a href="{{ url('/about') }}" class="text-gray-700 hover:text-blue-600 mx-3 transition">About</a>
                <a href="https://github.com/Steinbeck-Lab/DECIMER.ai" target="_blank" class="flex items-center text-gray-700 hover:text-blue-600 mx-3 transition">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" class="mr-1" viewBox="0 0 16 16">
                        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
                    </svg>
                    GitHub
                </a>
            </nav>
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

    <footer class="bg-white border-t border-gray-200 mt-12">
        <div class="max-w-screen-lg container mx-auto p-6">
            <div class="flex flex-col md:flex-row md:justify-between">
                <div class="mb-6 md:mb-0">
                    <h3 class="text-lg font-semibold mb-4">DECIMER.ai</h3>
                    <p class="text-sm text-gray-600 max-w-md">
                        DECIMER (Deep lEarning for Chemical IMagE Recognition) is developed by the 
                        <a href="https://cheminf.uni-jena.de/" target="_blank" class="text-blue-600 hover:text-blue-800 transition">Steinbeck group</a> 
                        at Friedrich Schiller University Jena.
                    </p>
                </div>
                
                <div class="grid grid-cols-2 gap-8 sm:grid-cols-3">
                    <div>
                        <h3 class="text-sm font-semibold mb-3 text-gray-500 uppercase">Project</h3>
                        <ul class="space-y-2">
                            <li>
                                <a href="https://github.com/Steinbeck-Lab/DECIMER.ai" class="text-gray-600 hover:text-blue-600 transition">GitHub Repository</a>
                            </li>
                            <li>
                                <a href="https://cheminf.uni-jena.de/research/deep-learning/" class="text-gray-600 hover:text-blue-600 transition">Research</a>
                            </li>
                        </ul>
                    </div>
                    
                    <div>
                        <h3 class="text-sm font-semibold mb-3 text-gray-500 uppercase">Legal</h3>
                        <ul class="space-y-2">
                            <li>
                                <a href="{{ route('privacy_policy') }}" class="text-gray-600 hover:text-blue-600 transition">Privacy Policy</a>
                            </li>
                            <li>
                                <a href="{{ route('impressum') }}" class="text-gray-600 hover:text-blue-600 transition">Impressum</a>
                            </li>
                        </ul>
                    </div>
                    
                    <div>
                        <h3 class="text-sm font-semibold mb-3 text-gray-500 uppercase">Contact</h3>
                        <ul class="space-y-2">
                            <li>
                                <a href="mailto:kohulan.rajan@uni-jena.de;christoph.steinbeck@uni-jena.de" class="text-gray-600 hover:text-blue-600 transition">Email us</a>
                            </li>
                            <li>
                                <a href="https://github.com/Steinbeck-Lab/DECIMER.ai/issues" class="text-gray-600 hover:text-blue-600 transition">Report Issues</a>
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
            
            <div class="mt-8 border-t border-gray-200 pt-6 flex flex-col md:flex-row items-center justify-between">
                <p class="text-sm text-gray-500">
                    &copy; {{ date('Y') }} DECIMER Project. All rights reserved.
                </p>
                <div class="flex space-x-6 mt-4 md:mt-0">
                    <a href="https://github.com/Steinbeck-Lab/DECIMER.ai" class="text-gray-500 hover:text-gray-700">
                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
                        </svg>
                    </a>
                </div>
            </div>
        </div>
    </footer>
    
</body>

</html>