<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use DateTime;

class ProblemReportController extends Controller
{
    public function ProblemReport()
    {
        return view('index');
    }

    public function copy_file_to_problem_report_storage(string $path){
        // Takes file path and moves the corresponding file to a separate directory
        // to save it from the temporary file deletion routine
        $media_dir = '../storage/app/public/media/';
        $orig_path = $media_dir . basename($path);
        $problem_report_storage_dir = '../storage/app/public/reported_results/';
        
        // Ensure the reported_results directory exists
        if (!is_dir($problem_report_storage_dir)) {
            mkdir($problem_report_storage_dir, 0775, true);
        }
        
        $dest_path = $problem_report_storage_dir . basename($path);
        
        if (file_exists($orig_path)) {
            copy($orig_path, $dest_path);
        }
    }

    public function ProblemReportPost(Request $request)
    {        
        try {
            // Use timestamp as ID
            $now = new DateTime();
            $now = $now->getTimestamp();
            
            // Move the processed files to a different location
            $requestData = $request->all();
            $smiles = $requestData['smiles'] ?? '';
            $structure_depiction_img_path = $requestData['structure_depiction_img_path'] ?? '';
            
            if (empty($smiles) || empty($structure_depiction_img_path)) {
                return response()->json(['error' => 'Missing required data'], 400);
            }
            
            // Extract just the SMILES string (remove any extra data)
            $smiles_parts = explode(" ", $smiles);
            $smiles = $smiles_parts[0];

            // Write mol file based on given smiles representation of molecule
            $mol_file_path = $this->WriteMolFile($structure_depiction_img_path, $smiles, $now);

            // Copy mol file and structure image to problem report storage
            if ($mol_file_path && file_exists($mol_file_path)) {
                $this->copy_file_to_problem_report_storage($mol_file_path);
            }
            $this->copy_file_to_problem_report_storage($structure_depiction_img_path);
            
            return response()->json(['success' => true, 'message' => 'Problem report submitted']);
            
        } catch (\Exception $e) {
            return response()->json(['error' => $e->getMessage()], 500);
        }
    }
    
    private function WriteMolFile(string $file_name, string $smiles, int $timestamp)
    {
        try {
            $structure_im_name = basename($file_name);
            $mol_file_path = '../storage/app/public/media/' . $structure_im_name . '_' . $timestamp . '.mol';
            
            // Convert SMILES to MOL using Python script
            $command = 'python3 ../app/Python/check_smiles_validity.py';
            $mol_data = exec($command . ' ' . escapeshellarg(json_encode([$smiles])));
            
            if (!empty($mol_data)) {
                $mol_file = fopen($mol_file_path, "w");
                fwrite($mol_file, $mol_data);
                fclose($mol_file);
                return $mol_file_path;
            }
            
            return null;
        } catch (\Exception $e) {
            return null;
        }
    }
}