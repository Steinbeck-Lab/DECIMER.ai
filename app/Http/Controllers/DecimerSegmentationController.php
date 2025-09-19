<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use DateTime;
use Illuminate\Support\Facades\Log;

class DecimerSegmentationController extends Controller
{
    public function DecimerSegmentation()
    {
        return view('index');
    }

    public function LogSegmentationProcesses(int $num_pages, int $num_structures){
        $now = new DateTime();
        $now = $now->getTimestamp();
        file_put_contents('decimer_segmentation_log.tsv', $now . "\t" . $num_pages . "\t" . $num_structures . "\n", FILE_APPEND | LOCK_EX);
    }

    public function SegmentChemicalStructures(array $img_paths) {
        Log::info('=== SegmentChemicalStructures DEBUG ===');
        Log::info('Input paths: ' . json_encode($img_paths));
        
        try {
            $json_input = json_encode($img_paths);
            Log::info('JSON input: ' . $json_input);
            
            $command = 'python3 ../app/Python/decimer_segmentation_client.py ' . escapeshellarg($json_input);
            Log::info('Command: ' . $command);
            
            $output = [];
            $return_code = 0;
            exec($command . ' 2>&1', $output, $return_code);
            
            Log::info('Return code: ' . $return_code);
            Log::info('Raw output: ' . json_encode($output));
            
            if ($return_code !== 0) {
                Log::error('Python script failed with return code: ' . $return_code);
                Log::error('Output: ' . implode("\n", $output));
                return [];
            }
            
            $json_output = end($output);
            Log::info('Last line of output: ' . var_export($json_output, true));
            
            if (empty($json_output)) {
                Log::error('Empty output from Python script');
                return [];
            }
            
            $structure_depiction_img_paths = json_decode($json_output, true);
            Log::info('Decoded result: ' . json_encode($structure_depiction_img_paths));
            Log::info('Result type: ' . gettype($structure_depiction_img_paths));
            
            if (!is_array($structure_depiction_img_paths)) {
                Log::error('Invalid JSON output from Python script: ' . $json_output);
                return [];
            }
            
            Log::info('Final result count: ' . count($structure_depiction_img_paths));
            return $structure_depiction_img_paths;
            
        } catch (\Exception $e) {
            Log::error('Exception in SegmentChemicalStructures: ' . $e->getMessage());
            Log::error('Stack trace: ' . $e->getTraceAsString());
            return [];
        }
    }

    public function DecimerSegmentationPost(Request $request)
    {   
        Log::info('=== DecimerSegmentationPost START ===');
        
        try {
            $requestData = $request->all();
            Log::info('Request data: ' . json_encode($requestData));
            
            $img_paths = $requestData['img_paths'] ?? null;
            Log::info('img_paths raw: ' . var_export($img_paths, true));
            
            if (empty($img_paths)) {
                Log::error('No image paths provided');
                return back()->withErrors(['No image paths provided']);
            }
            
            $img_paths = str_replace(' ', '', $img_paths);
            Log::info('img_paths after space removal: ' . $img_paths);
            
            $img_paths = json_decode($img_paths, true);
            Log::info('img_paths after decode: ' . json_encode($img_paths));
            
            if (!is_array($img_paths)) {
                Log::error('Invalid image paths format');
                return back()->withErrors(['Invalid image paths format']);
            }

            ini_set('max_execution_time', 300);

            $structure_depiction_img_paths = $this->SegmentChemicalStructures($img_paths);
            Log::info('Segmentation result: ' . json_encode($structure_depiction_img_paths));

            $num_pages = is_countable($img_paths) ? count($img_paths) : 0;
            $num_structures = is_countable($structure_depiction_img_paths) ? count($structure_depiction_img_paths) : 0;
            
            Log::info('Counts - Pages: ' . $num_pages . ', Structures: ' . $num_structures);
            
            $this->LogSegmentationProcesses($num_pages, $num_structures);
       
            return back()
                ->with('img_paths', json_encode($img_paths))
                ->with('structure_depiction_img_paths', json_encode($structure_depiction_img_paths))
                ->with('has_segmentation_already_run', "true")
                ->with('single_image_upload', $requestData['single_image_upload']);
                
        } catch (\Exception $e) {
            Log::error('Exception in DecimerSegmentationPost: ' . $e->getMessage());
            Log::error('Stack trace: ' . $e->getTraceAsString());
            return back()->withErrors(['Processing failed: ' . $e->getMessage()]);
        }
    }
}