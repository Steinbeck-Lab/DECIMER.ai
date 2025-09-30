<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\FileUploadController;
use App\Http\Controllers\DecimerController;
use App\Http\Controllers\DecimerSegmentationController;
use App\Http\Controllers\ResultArchiveController;
use App\Http\Controllers\AboutController;
use App\Http\Controllers\ProblemReportController;
use App\Http\Controllers\ClipboardController;

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

// Home route
Route::get('/', function () {
    return view('index');
})->name('home');

// File upload routes
Route::get('/file-upload', [FileUploadController::class, 'fileUpload'])->name('file.upload');
Route::post('/file-upload', [FileUploadController::class, 'fileUploadPost'])->name('file.upload.post');

// Clipboard paste route
Route::post('/clipboard-paste', [ClipboardController::class, 'store'])->name('clipboard.paste.post');

// DECIMER segmentation routes
Route::get('/decimer-segmentation', [DecimerSegmentationController::class, 'DecimerSegmentation'])->name('decimer.segmentation');
Route::post('/decimer-segmentation', [DecimerSegmentationController::class, 'DecimerSegmentationPost'])->name('decimer.segmentation.post');

// DECIMER OCSR routes
Route::get('/decimer-ocsr', [DecimerController::class, 'DecimerOCSR'])->name('decimer.ocsr');
Route::post('/decimer-ocsr', [DecimerController::class, 'DecimerOCSRPost'])->name('decimer.ocsr.post');

// Archive creation routes
Route::get('/archive-creation', [ResultArchiveController::class, 'archiveCreation'])->name('archive.creation');
Route::post('/archive-creation', [ResultArchiveController::class, 'archiveCreationPost'])->name('archive.creation.post');

// Problem report routes
Route::get('/problem-report', [ProblemReportController::class, 'ProblemReport'])->name('problem.report');
Route::post('/problem-report', [ProblemReportController::class, 'ProblemReportPost'])->name('problem.report.post');

// About, Privacy Policy and Impressum routes
Route::get('/about', [AboutController::class, 'about'])->name('about');
Route::get('/privacy_policy', function () {
    return view('privacy_policy');
})->name('privacy_policy');
Route::get('/impressum', function () {
    return view('impressum');
})->name('impressum');

URL::forceScheme('https');