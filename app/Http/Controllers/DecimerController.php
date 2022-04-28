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
        // Get paths of images to process
        $requestData = $request->all();
        $img_paths = $requestData['img_paths'];
        $structure_depiction_img_paths_str = $requestData['structure_depiction_img_paths'];
        
        
        // Limit amount of structures that are processed to avoid abuse of web app
        $structure_depiction_img_paths = json_decode($structure_depiction_img_paths_str);
        $num_structures = count($structure_depiction_img_paths);
        if ($num_structures > 20){
            $structure_depiction_img_paths = array_slice($structure_depiction_img_paths, 0, 20);
            }
        $structure_depiction_img_paths = json_encode($structure_depiction_img_paths);
        // Comment out all lines from last comment to this one if you want to
        // enable processing more than 20 chemical structure depictions

        $structure_depiction_img_paths = str_replace(' ', '', $structure_depiction_img_paths);

        // Send request to local DECIMER predictor server
        $decimer_command = 'python3 ../app/Python/decimer_predictor_client.py ';
        $smiles_array = exec($decimer_command . $structure_depiction_img_paths);
        
        $smiles_array = json_decode($smiles_array);
        // If there were more than 20 structures to process: Fill up with empty str
        if ($num_structures > 20){
            for ($i = 0; $i < $num_structures - 20; ++$i){
                array_push($smiles_array, "");
            }
            $num_structures = 20;
        }
        // Comment out all lines from last comment to this one if you want to
        // enable processing more than 20 chemical structure depictions
        $smiles_array = json_encode($smiles_array);

        // Write data about how many structures have been processed
        $now = new DateTime();
        $now = $now->getTimestamp();
        file_put_contents('decimer_ocsr_log.tsv', $now . "\t" . $num_structures . "\n", FILE_APPEND | LOCK_EX);
        
        return back()
            ->with('img_paths', $img_paths)
            ->with('structure_depiction_img_paths', $structure_depiction_img_paths_str)
            ->with('smiles_array', $smiles_array);
    }
}