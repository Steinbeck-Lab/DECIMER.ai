<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;
use App\Http\Controllers\Controller;

class ClipboardController extends Controller
{
    /**
     * Handle the incoming clipboard paste request.
     *
     * @param  \Illuminate\Http\Request  $request
     * @return \Illuminate\Http\RedirectResponse
     */
    public function store(Request $request)
    {
        try {
            // Validate the request
            $request->validate([
                'clipboard_image' => 'required|string'
            ]);

            // Get the base64 image data
            $image_data = $request->clipboard_image;
            
            // Remove the data:image/png;base64, part
            $image_data = substr($image_data, strpos($image_data, ',') + 1);
            
            // Decode base64 data
            $image = base64_decode($image_data);
            
            // Generate unique filename
            $filename = 'clipboard_' . time() . '.png';
            
            // Store the image
            Storage::disk('public')->put('media/' . $filename, $image);
            
            // Create the img_paths array with just this image
            $img_paths = json_encode([storage_path('app/public/media/' . $filename)]);
            
            // Store in session
            session([
                'img_paths' => $img_paths,
                'single_image_upload' => 'true'
            ]);

            return redirect()->route('home');
            
        } catch (\Exception $e) {
            return redirect()->route('home')->withErrors(['Error processing clipboard image: ' . $e->getMessage()]);
        }
    }
}