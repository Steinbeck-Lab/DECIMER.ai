@extends('layouts.default')

@section('page-content')
<div class="bg-white">
    <!-- Hero section with DECIMER logo and brief introduction -->
    <div class="relative isolate px-6 pt-14 lg:px-8">
        <div class="absolute inset-x-0 -top-40 -z-10 transform-gpu overflow-hidden blur-3xl sm:-top-80" aria-hidden="true">
            <div class="relative left-[calc(50%-11rem)] aspect-[1155/678] w-[36.125rem] -translate-x-1/2 rotate-[30deg] bg-gradient-to-tr from-blue-500 to-purple-300 opacity-20 sm:left-[calc(50%-30rem)] sm:w-[72.1875rem]"></div>
        </div>
        
        <div class="mx-auto max-w-4xl py-8 sm:py-16">
            <div class="text-center">
                <img src="DECIMER.png" alt="DECIMER Logo" class="mx-auto mb-8 w-64">
                <h1 class="text-4xl font-bold tracking-tight text-gray-900 sm:text-6xl">DECIMER.ai</h1>
                <p class="mt-6 text-lg leading-8 text-gray-600">
                    An open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications.
                </p>
            </div>
        </div>
    </div>

    <!-- How to use section -->
    <div class="py-16 bg-gray-50">
        <div class="max-w-screen-lg container mx-auto px-6">
            <h2 class="text-4xl font-bold mb-6">How to use the DECIMER web app</h2>
            <div class="text-lg text-gray-600">
                <p class="mb-6">
                    Just upload a PDF document or one or multiple images that contain chemical structure
                    depictions. If a PDF document is uploaded, DECIMER Segmentation is used to detect
                    and segment all chemical structure depictions. The detected or uploaded chemical structure
                    depictions are processed using the powerful OCSR engine of DECIMER V2. The chemical
                    structure depictions and the corresponding SMILES representation are presented for your review.
                </p>
                
                <p class="mb-6">
                    You can edit the structures according to your needs in the 
                    <a href="{{ url('https://lifescience.opensource.epam.com/ketcher/') }}" target="_blank" class="text-blue-600 hover:text-blue-800 transition">
                        Ketcher chemical structure editor
                    </a>
                    windows before downloading the segmented images and the corresponding mol files.
                    Additionally, the IUPAC names of the chemical structures can be resolved using STOUT V2.
                </p>
            </div>
        </div>
    </div>

    <!-- Cite us section -->
    <div class="py-16 bg-white">
        <div class="max-w-screen-lg container mx-auto px-6">
            <h2 class="text-4xl font-bold mb-6">Cite us</h2>
            <h3 class="text-xl mb-6 text-gray-700">If our toolkit helped your work, please cite our publications.</h3> 
            <div class="bg-gray-100 p-6 rounded-lg mb-8">
                <ul class="space-y-4 list-disc pl-6">
                    <li>
                        <a href="{{ url('https://doi.org/10.1038/s41467-023-40782-0') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">DECIMER.ai: an open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications</span><br> 
                            Rajan, K., Brinkhaus, H.O., Agea, I.A. Zielesny, A., Steinbeck, C.
                            <span class="italic">Nat Commun</span>, 
                            <span class="font-bold">14</span>, 5045 (2023).
                        </a>
                    </li>
                    <li>
                        <a href="{{ url('https://doi.org/10.1186/s13321-024-00872-7') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">Advancements in hand-drawn chemical structure recognition through an enhanced DECIMER architecture.</span><br>
                            Rajan, K., Brinkhaus, H.O., Zielesny, A., Steinbeck, C.
                            <span class="italic">J Cheminform</span>, 
                            <span class="font-bold">16</span>, 78 (2024).
                        </a>
                    </li>
                    <li>
                        <a href="{{ url('https://doi.org/10.1186/s13321-020-00469-w') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">DECIMER: towards deep learning for chemical image recognition</span><br>
                            Rajan, K., Zielesny, A., Steinbeck, C.
                            <span class="italic">J Cheminform</span>, 
                            <span class="font-bold">12</span>, 65 (2020).
                        </a>
                    </li>
                    <li>
                        <a href="{{ url('https://doi.org/10.1186/s13321-021-00496-1') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">DECIMER-Segmentation: Automated extraction of chemical structure depictions from scientific literature</span><br>
                            Rajan, K., Brinkhaus, H.O., Sorokina, M. et al. 
                            <span class="italic">J Cheminform</span>, 
                            <span class="font-bold">13</span>, 20 (2021).
                        </a>
                    </li>
                    <li>
                        <a href="{{ url('https://doi.org/10.1186/s13321-021-00538-8') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">DECIMER 1.0: deep learning for chemical image recognition using transformers</span><br>
                            Rajan, K., Zielesny, A., Steinbeck, C.
                            <span class="italic">J Cheminform</span>, 
                            <span class="font-bold">13</span>, 61 (2021).
                        </a>
                    </li>
                    <li>
                        <a href="{{ url('https://doi.org/10.1186/s13321-021-00512-4') }}" class="text-lg text-gray-800 hover:text-blue-600" target="_blank">
                            <span class="font-semibold">STOUT: SMILES to IUPAC names using neural machine translation</span><br> 
                            Rajan, K., Zielesny, A., Steinbeck, C. 
                            <span class="italic">J Cheminform</span>, 
                            <span class="font-bold">13</span>, 34 (2021).
                        </a>
                    </li>
                </ul>
            </div>
            <div class="flex justify-center">
                <a href="{{ url('https://cheminf.uni-jena.de/research/deep-learning/') }}" target="_blank" class="bg-blue-600 text-white text-center py-3 px-6 rounded-md hover:bg-blue-700 transition">Learn more about our research</a>
            </div>
        </div>
    </div>

    <!-- Project components section with logos -->
    <div class="py-16 bg-gray-50">
        <div class="max-w-screen-lg container mx-auto px-6">
            <h2 class="text-4xl font-bold mb-12 text-center">Project Components</h2>
            <!-- Logos with links -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
                <div class="text-center">
                    <a href={{ url("https://github.com/Kohulan/DECIMER-Image-Segmentation") }} target="_blank" class="block hover:opacity-80 transition">
                        <img src="DECIMER_Segmentation_logo.png" alt="DECIMER Segmentation Logo" class="mx-auto mb-4 max-h-32"/>
                        <h3 class="text-xl font-semibold mb-2">DECIMER Segmentation</h3>
                        <p class="text-gray-600">Chemical structure detection and extraction</p>
                    </a>
                </div>
                <div class="text-center">
                    <a href={{ url("https://github.com/Kohulan/Smiles-TO-iUpac-Translator") }} target="_blank" class="block hover:opacity-80 transition">
                        <img src="https://github.com/Kohulan/Smiles-TO-iUpac-Translator/blob/development/docs/_static/STOUT.png?raw=true" alt="STOUT Logo" class="mx-auto mb-4 max-h-32"/>
                        <h3 class="text-xl font-semibold mb-2">STOUT</h3>
                        <p class="text-gray-600">SMILES to IUPAC name translation</p>
                    </a>
                </div>
                <div class="text-center">
                    <a href={{ url("https://github.com/Kohulan/DECIMER-Image_Transformer") }} target="_blank" class="block hover:opacity-80 transition">
                        <img src="DECIMER_Transformer_logo.png" alt="DECIMER OCSR Logo" class="mx-auto mb-4 max-h-32"/>
                        <h3 class="text-xl font-semibold mb-2">DECIMER Transformer</h3>
                        <p class="text-gray-600">Optical chemical structure recognition</p>
                    </a>
                </div>
            </div>
        </div>
    </div>

    <!-- About section -->
    <div class="py-16 bg-white">
        <div class="max-w-screen-lg container mx-auto px-6">
            <h2 class="text-4xl font-bold mb-6">About DECIMER</h2>
            <div class="text-lg text-gray-600">
                <p class="mb-6">
                    Deep Learning for Chemical Image Recognition (DECIMER) is a step towards automated chemical 
                    image segmentation and recognition. DECIMER is actively developed and maintained by the
                    <a href="{{ url('https://cheminf.uni-jena.de/research/deep-learning/') }}" target="_blank" class="text-blue-600 hover:text-blue-800 transition">Steinbeck group</a> at the
                    <a href="{{ url('https://www.uni-jena.de/') }}" target="_blank" class="text-blue-600 hover:text-blue-800 transition">Friedrich Schiller University Jena</a>.
                </p>
                <p class="mb-6">
                    You need to have the right granted by the publisher of the uploaded documents and images to use them for data mining.
                    We do not store or use the data for anything other than automated processing and display of results in the web app. 
                    Your documents and images are only saved for one hour unless a problem is reported. If a problem is reported,
                    we will use the reported image to analyse errors before deleting them.
                </p>
                <p>
                    Google Analytics is used to get some basic statistics about the number of visitors.
                    You can look up a more detailed description of what happens with your data in our 
                    <a href="{{ route('privacy_policy') }}" target="_blank" class="text-blue-600 hover:text-blue-800 transition">privacy policy</a>. 
                    German law requires us to provide some information about who we are: 
                    <a href="{{ route('impressum') }}" target="_blank" class="text-blue-600 hover:text-blue-800 transition">Impressum - Legal Disclosure</a>.
                </p>
            </div>
        </div>
    </div>
</div>
@endsection