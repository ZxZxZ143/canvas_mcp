# Local-only comparison. No credential value, length, hash, header or error detail.
param([System.Security.SecureString]$Value)

$canvasComparisonOwnsValue = $null -eq $Value
$canvasComparisonPointer = [IntPtr]::Zero
$canvasComparisonA = $null
$canvasComparisonB = $null
try {
    if ($canvasComparisonOwnsValue) {
        $Value = Read-Host 'Canvas token (local comparison only)' -AsSecureString
    }
    $canvasComparisonPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    $canvasComparisonA = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($canvasComparisonPointer)
    $canvasComparisonB = [System.Net.NetworkCredential]::new('', $Value).Password
    [pscustomobject]@{
        length_equal = $canvasComparisonA.Length -eq $canvasComparisonB.Length
        content_equal = [string]::Equals($canvasComparisonA, $canvasComparisonB, [StringComparison]::Ordinal)
        empty_a = [string]::IsNullOrEmpty($canvasComparisonA)
        empty_b = [string]::IsNullOrEmpty($canvasComparisonB)
    }
} catch {
    # Do not render an exception which could include a supplied secret.
    [pscustomobject]@{ length_equal = $false; content_equal = $false; empty_a = $true; empty_b = $true }
} finally {
    if ($canvasComparisonPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($canvasComparisonPointer)
    }
    $canvasComparisonA = $null
    $canvasComparisonB = $null
    if ($canvasComparisonOwnsValue -and $null -ne $Value) { $Value.Dispose() }
}
