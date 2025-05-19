<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\FileUploadController;
use App\Http\Controllers\DecimerSegmentationController;
use App\Http\Controllers\DecimerController;
use App\Http\Controllers\StoutController;
use App\Http\Controllers\ResultArchiveController;
use App\Http\Controllers\ClipboardController;
use App\Http\Controllers\ProblemReportController;
use App\Http\Controllers\AboutController;

/*
|--------------------------------------------------------------------------
| Web Routes
|--------------------------------------------------------------------------
|
| Here is where you can register web routes for your application. These
| routes are loaded by the RouteServiceProvider within a group which
| contains the "web" middleware group. Now create something great!
|
*/

Route::get('/', [FileUploadController::class, 'fileUpload'])->name('home');
Route::post('/upload', [FileUploadController::class, 'fileUploadPost'])->name('file.upload.post');

Route::get('/about', [AboutController::class, 'about'])->name('about');

Route::get('/DecIMER/segmentation', [DecimerSegmentationController::class, 'DecimerSegmentation'])->name('decimer.segmentation');
Route::post('/DecIMER/segmentation', [DecimerSegmentationController::class, 'DecimerSegmentationPost'])->name('decimer.segmentation.post');

Route::get('/DecIMER/OCSR', [DecimerController::class, 'DecimerOCSR'])->name('decimer.ocsr');
Route::post('/DecIMER/OCSR', [DecimerController::class, 'DecimerOCSRPost'])->name('decimer.ocsr.post');

Route::get('/STOUT/iupac', [StoutController::class, 'Stout'])->name('stout.iupac');
Route::post('/STOUT/iupac', [StoutController::class, 'StoutPost'])->name('stout.iupac.post');

Route::get('/download/results', [ResultArchiveController::class, 'archiveCreation'])->name('archive.creation');
Route::post('/download/results', [ResultArchiveController::class, 'archiveCreationPost'])->name('archive.creation.post');

Route::post('/clipboard/paste', [ClipboardController::class, 'store'])->name('clipboard.paste.post');

Route::get('/problem/report', [ProblemReportController::class, 'ProblemReport'])->name('problem.report');
Route::post('/problem/report', [ProblemReportController::class, 'ProblemReportPost'])->name('problem.report.post');

Route::view('/privacy_policy', 'privacy_policy')->name('privacy_policy');
Route::view('/impressum', 'impressum')->name('impressum');

URL::forceScheme('https');
