<?php

namespace App\Http\Controllers;

use App\Http\Controllers\Controller;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Storage;

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
                'clipboard_image' => 'required|string',
            ]);

            // Get the base64 image data
            $image_data = $request->clipboard_image;

            // Verify it's a valid base64 image
            if (strpos($image_data, 'data:image') === false) {
                return redirect()->route('home')->with('errors', ['Invalid image data']);
            }

            // Remove the data:image/png;base64, part
            $image_data = substr($image_data, strpos($image_data, ',') + 1);

            // Decode base64 data
            $image = base64_decode($image_data);
            
            if ($image === false) {
                return redirect()->route('home')->with('errors', ['Failed to decode image data']);
            }

            // Generate unique filename
            $filename = 'clipboard_' . time() . '.png';

            // Store the image
            Storage::disk('public')->put('media/' . $filename, $image);

            // Create the img_paths array with just this image
            $img_paths = json_encode([storage_path('app/public/media/' . $filename)]);

            // Store in session and redirect
            return redirect()->route('home')
                ->with('img_paths', $img_paths)
                ->with('single_image_upload', 'true');

        } catch (\Exception $e) {
            return redirect()->route('home')->with('errors', ['Error processing clipboard image: ' . $e->getMessage()]);
        }
    }
}