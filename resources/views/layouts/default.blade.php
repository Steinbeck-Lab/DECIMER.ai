<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="ie-edge">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <meta http-equiv="Pragma" content="no-cache" />
    <meta http-equiv="Expires" content="0" />
    <link rel="stylesheet" href="{{ asset('css/app.css') }}">
    <link rel="stylesheet" href="{{ asset('css/tailwind.min.css') }}">
    <link rel="stylesheet" href="{{ asset('css/bootstrap.min.css') }}">
    <script src="/js/app.js?1238"></script>
    <script src="/js/jquery.min.js"></script>
    <link rel="icon" href="{{ asset('DECIMER_favicon.png') }}">
    <title>DECIMER Web Application</title>
    <!--Global site tag (gtag.js) - Google Analytics-->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-VKSWMKC79R"></script>
    <script>
        window.dataLayer = window.dataLayer || [];
        function gtag() { dataLayer.push(arguments); }
        gtag('js', new Date());
        gtag('config', 'G-VKSWMKC79R');
    </script>
    <script>
        var _paq = window._paq = window._paq || [];
        _paq.push(['trackPageView']);
        _paq.push(['enableLinkTracking']);
        (function () {
            var u = "//matomo.nfdi4chem.de/";
            _paq.push(['setTrackerUrl', u + 'matomo.php']);
            _paq.push(['setSiteId', '2']);
            var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];
            g.async = true; g.src = u + 'matomo.js'; s.parentNode.insertBefore(g, s);
        })();
    </script>

    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }

        .header-container {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 16px;
            border-bottom: 1px solid #e5e7eb;
            background-color: white;
        }

        .nav-links {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .nav-link {
            color: #5f6368;
            text-decoration: none;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 14px;
            transition: background-color 0.2s;
        }

        .nav-link:hover {
            background-color: #f1f3f4;
            color: #202124;
        }

        .header-center {
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn-primary {
            background-color: #4285f4;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            transition: background-color 0.2s;
        }

        .btn-primary:hover {
            background-color: #3367d6;
        }

        .btn-secondary {
            background-color: #f1f3f4;
            color: #5f6368;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            transition: background-color 0.2s;
        }

        .btn-secondary:hover {
            background-color: #e8eaed;
        }

        .main-content {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 20px 0;
            flex: 1;
        }

        .footer {
            background-color: #f8f9fa;
            border-top: 1px solid #e5e7eb;
            padding: 10px 24px;
            margin-top: auto;
        }

        .footer-content {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .footer-description {
            font-size: 13px;
            color: #5f6368;
            text-align: center;
            margin-bottom: 8px;
        }

        .footer-bottom {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-top: 8px;
            border-top: 1px solid #e5e7eb;
        }

        .footer-copyright {
            font-size: 12px;
            color: #5f6368;
        }

        .footer-links {
            display: flex;
            gap: 16px;
        }

        .footer-link {
            font-size: 12px;
            color: #5f6368;
            text-decoration: none;
        }

        .footer-link:hover {
            color: #4285f4;
        }

        .loading-indicator {
            display: flex;
            align-items: center;
            gap: 8px;
            background-color: #f1f3f4;
            padding: 4px 12px;
            border-radius: 16px;
        }

        .loading-icon {
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            from {
                transform: rotate(0deg);
            }
            to {
                transform: rotate(360deg);
            }
        }

        .animate-pulse {
            animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }

        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.5;
            }
        }

        .animate-float {
            animation: float 6s ease-in-out infinite;
        }

        @keyframes float {
            0% {
                transform: translateY(0px);
            }
            50% {
                transform: translateY(-10px);
            }
            100% {
                transform: translateY(0px);
            }
        }

        @media (max-width: 768px) {
            .footer-bottom {
                flex-direction: column;
                gap: 12px;
                align-items: flex-start;
            }

            .footer-links {
                width: 100%;
                justify-content: flex-end;
            }
        }
    </style>
</head>

<body class="bg-white flex flex-col min-h-screen">
    <!-- Header -->
    <header class="header-container sticky top-0 z-50">
        <!-- Left navigation links -->
        <nav class="nav-links">
            <a href="{{ route('home') }}" class="nav-link">Home</a>
            <a href="{{ url('/about') }}" class="nav-link">About</a>
            <a href="https://github.com/Steinbeck-Lab/DECIMER.ai" target="_blank" class="nav-link flex items-center"
                style="display: inline-flex; flex-direction: row; align-items: center; white-space: nowrap;">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16"
                    style="margin-right: 4px; display: inline-block;">
                    <path
                        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
                </svg>
                <span style="display: inline;">GitHub</span>
            </a>
        </nav>

        <!-- Center loading indicator -->
        <div class="header-center">
            <div class="loading-indicator" id="status_container" style="display: none;">
                <svg xmlns="http://www.w3.org/2000/svg" class="loading-icon h-4 w-4 text-blue-500" viewBox="0 0 24 24"
                    fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                    <path d="M12 6v6l4 2"></path>
                </svg>
                <div class="text-sm font-medium text-gray-700" id="loading_text"></div>
            </div>
        </div>

        <!-- Right action buttons -->
        <div class="header-actions">
            @if (Session::get('smiles_array'))
                <!-- Download Button -->
                <form id="archive_creation_form" action="{{ route('archive.creation.post') }}" method="POST"
                    enctype="multipart/form-data">
                    @csrf
                    <input type="hidden" name="img_paths" value="{{ Session::get('img_paths') }}" />
                    <input type="hidden" name="structure_depiction_img_paths"
                        value="{{ Session::get('structure_depiction_img_paths') }}" />
                    <input type="hidden" id="smiles_array" name="smiles_array" value="{{ Session::get('smiles_array') }}" />
                    <input type="hidden" id="header_download_form_molfile_array" name="mol_file_array" />
                    <input type="hidden" id="classifier_result_array" name="classifier_result_array"
                        value="{{ Session::get('classifier_result_array') }}" />
                    <input type="hidden" id="header_download_form_has_segmentation_already_run"
                        name="has_segmentation_already_run" />
                    <input type="hidden" id="header_download_form_single_image_upload" name="single_image_upload" />
                    <?php
                        $smiles_session = Session::get('smiles_array');
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
                    <button class="btn-secondary"
                        onclick="submit_with_updated_molfiles('{{ $num_ketcher_frames }}', 'header_download_form_molfile_array')">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 inline mr-1" fill="none" viewBox="0 0 24 24"
                            stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        Download
                    </button>
                </form>
            @endif

            <!-- Loading indicator -->
            <img src="loading_icon_mini.gif" alt="Loading icon" id="header_loading_icon"
                style="display: none; visibility: hidden;" />
            <div class="text-sm text-gray-800" id="loading_text" style="display: inline;"></div>
        </div>
    </header>

    <!-- Main Content -->
    <main class="main-content">
        @yield('page-content')
    </main>

    <!-- Footer -->
    <footer class="footer">
        <div class="footer-content">
            <div class="footer-description">
                DECIMER (Deep lEarning for Chemical IMagE Recognition) is developed with
                <svg xmlns="http://www.w3.org/2000/svg" class="inline-block h-4 w-4 text-yellow-700 animate-float"
                    fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                        d="M18 8h1a4 4 0 010 8h-1M2 8h16v9a4 4 0 01-4 4H6a4 4 0 01-4-4V8zm4-7v3M12 1v3M8 1v3" />
                </svg>
                by the <a href="https://cheminf.uni-jena.de/"
                    class="text-indigo-600 hover:text-indigo-800 font-semibold transition">Steinbeck group</a> at
                <a href="https://www.uni-jena.de/"
                    class="text-indigo-600 hover:text-indigo-800 font-semibold transition">Friedrich Schiller University
                    Jena</a>.
                You can contact us directly via <a
                    href="mailto:kohulan.rajan@uni-jena.de;christoph.steinbeck@uni-jena.de"
                    class="text-indigo-600 hover:text-indigo-800 transition">email</a> or
                create an <a href="https://github.com/Steinbeck-Lab/DECIMER.ai/issues"
                    class="text-indigo-600 hover:text-indigo-800 transition">issue on GitHub</a>.
            </div>

            <div class="footer-bottom">
                <div class="footer-copyright">
                    &copy; 2025 <span class="font-semibold">DECIMER Project</span>. All rights reserved.
                </div>

                <div class="footer-links">
                    <a href="{{ route('privacy_policy') }}" class="footer-link">Privacy Policy</a>
                    <span class="text-gray-400">|</span>
                    <a href="{{ route('impressum') }}" class="footer-link">Impressum</a>
                </div>
            </div>
        </div>
    </footer>
</body>

</html>