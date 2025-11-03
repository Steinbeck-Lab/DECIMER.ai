<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use DateTime;

class DecimerController extends Controller
{
    public function DecimerOCSR()
    {
        return view('index');
    }

    public function DecimerOCSRPost(Request $request)
    {
        $requestData = $request->all();
        $img_paths = $requestData['img_paths'] ?? '';
        $structure_depiction_img_paths_str = $requestData['structure_depiction_img_paths'] ?? '';

        if (empty($structure_depiction_img_paths_str)) {
            return back()->withErrors(['No structure images to process']);
        }

        $structure_depiction_img_paths = json_decode($structure_depiction_img_paths_str, true);
        if (!is_array($structure_depiction_img_paths)) {
            return back()->withErrors(['Invalid structure image paths']);
        }

        $num_structures = count($structure_depiction_img_paths);
        
        // Limit to 20 structures
        if ($num_structures > 20) {
            $structure_depiction_img_paths = array_slice($structure_depiction_img_paths, 0, 20);
        }

        // CRITICAL FIX: Pass as proper JSON, not modified string
        $json_input = json_encode($structure_depiction_img_paths);
        
        try {
            // OCSR - Generate SMILES
            $decimer_command = 'python3 ../app/Python/decimer_predictor_client.py ' . escapeshellarg($json_input);
            $smiles_output = shell_exec($decimer_command);
            $smiles_array = json_decode($smiles_output, true);
            
            if (!is_array($smiles_array)) {
                $smiles_array = array_fill(0, count($structure_depiction_img_paths), '');
            }

            // If more than 20 structures, pad with empty strings
            if ($num_structures > 20) {
                for ($i = 0; $i < $num_structures - 20; $i++) {
                    $smiles_array[] = "";
                }
            }
            
            $smiles_array_json = json_encode($smiles_array);

            // Check validity
            $check_validity_command = 'python3 ../app/Python/check_smiles_validity.py ' . escapeshellarg($smiles_array_json);
            $validity_output = shell_exec($check_validity_command);
            $validity_arr = json_decode($validity_output, true);
            
            if (!is_array($validity_arr)) {
                $validity_arr = array_fill(0, count($smiles_array), 'invalid');
            }

            // Get InChIKeys
            $get_inchikey_command = 'python3 ../app/Python/get_inchikey_list_from_smiles.py ' . escapeshellarg($smiles_array_json);
            $inchikey_output = shell_exec($get_inchikey_command);
            $inchikey_arr = json_decode($inchikey_output, true);
            
            if (!is_array($inchikey_arr)) {
                $inchikey_arr = array_fill(0, count($smiles_array), 'invalid');
            }

            // Classify structures
            $classifier_command = 'python3 ../app/Python/decimer_classifier_client.py ' . escapeshellarg($json_input);
            $classifier_output = shell_exec($classifier_command);
            $classifier_result_array = json_decode($classifier_output, true);
            
            if (!is_array($classifier_result_array)) {
                $classifier_result_array = array_fill(0, count($structure_depiction_img_paths), 'True');
            }

            // Count EXIF tags
            $count_exif_command = 'python3 ../app/Python/count_exif_tags.py ' . escapeshellarg($json_input);
            $num_exif_tags = (int)shell_exec($count_exif_command);

            // Log processing
            $now = new DateTime();
            $timestamp = $now->getTimestamp();
            $num_structures_processed = min($num_structures, 20);
            file_put_contents(storage_path('logs/decimer_ocsr_log.tsv'), $timestamp . "\t" . $num_structures_processed . "\t" . $num_exif_tags . "\n", FILE_APPEND | LOCK_EX);

            return back()
                ->with('img_paths', $img_paths)
                ->with('structure_depiction_img_paths', $structure_depiction_img_paths_str)
                ->with('smiles_array', $smiles_array_json)
                ->with('validity_array', json_encode($validity_arr))
                ->with('inchikey_array', json_encode($inchikey_arr))
                ->with('classifier_result_array', json_encode($classifier_result_array))
                ->with('has_segmentation_already_run', $requestData['has_segmentation_already_run'] ?? null)
                ->with('single_image_upload', $requestData['single_image_upload'] ?? null);

        } catch (\Exception $e) {
            return back()->withErrors(['OCSR processing failed: ' . $e->getMessage()]);
        }
    }
}