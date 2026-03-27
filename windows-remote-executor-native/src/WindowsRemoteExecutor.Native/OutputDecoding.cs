using System.Runtime.InteropServices;
using System.Text;

namespace WindowsRemoteExecutor.Native;

internal enum OutputEncodingPreference
{
    Auto,
    Utf8
}

internal sealed class DecodedOutput
{
    public string Text { get; init; } = string.Empty;
    public string EncodingLabel { get; init; } = "utf-8";
}

internal static class OutputDecoding
{
    private static readonly UTF8Encoding Utf8Strict = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);
    private static readonly UTF8Encoding Utf8NoBom = new(encoderShouldEmitUTF8Identifier: false);

    public static DecodedOutput Decode(byte[] bytes, OutputEncodingPreference preference)
    {
        var detected = DetectEncoding(bytes, preference);
        var offset = GetPreambleLength(bytes, detected.Encoding);
        return new DecodedOutput
        {
            Text = detected.Encoding.GetString(bytes, offset, bytes.Length - offset),
            EncodingLabel = detected.Label
        };
    }

    public static DetectedEncoding DetectEncoding(byte[] bytes, OutputEncodingPreference preference)
    {
        if (bytes.Length == 0)
        {
            return new DetectedEncoding(Utf8NoBom, "utf-8");
        }

        if (TryDetectBom(bytes, out var bomEncoding, out var bomLabel))
        {
            return new DetectedEncoding(bomEncoding, bomLabel);
        }

        if (LooksLikeUtf16LittleEndian(bytes))
        {
            return new DetectedEncoding(Encoding.Unicode, "utf-16le");
        }

        if (LooksLikeUtf16BigEndian(bytes))
        {
            return new DetectedEncoding(Encoding.BigEndianUnicode, "utf-16be");
        }

        if (LooksLikeUtf8(bytes))
        {
            return new DetectedEncoding(Utf8NoBom, "utf-8");
        }

        foreach (var encoding in EnumerateLegacyCandidates(preference))
        {
            return new DetectedEncoding(encoding, DescribeEncoding(encoding));
        }

        return new DetectedEncoding(Encoding.Default, DescribeEncoding(Encoding.Default));
    }

    private static bool TryDetectBom(byte[] bytes, out Encoding encoding, out string label)
    {
        if (bytes.Length >= 3 &&
            bytes[0] == 0xEF &&
            bytes[1] == 0xBB &&
            bytes[2] == 0xBF)
        {
            encoding = Utf8NoBom;
            label = "utf-8-bom";
            return true;
        }

        if (bytes.Length >= 2 &&
            bytes[0] == 0xFF &&
            bytes[1] == 0xFE)
        {
            encoding = Encoding.Unicode;
            label = "utf-16le-bom";
            return true;
        }

        if (bytes.Length >= 2 &&
            bytes[0] == 0xFE &&
            bytes[1] == 0xFF)
        {
            encoding = Encoding.BigEndianUnicode;
            label = "utf-16be-bom";
            return true;
        }

        encoding = Utf8NoBom;
        label = "utf-8";
        return false;
    }

    private static bool LooksLikeUtf8(byte[] bytes)
    {
        try
        {
            Utf8Strict.GetCharCount(bytes);
            return true;
        }
        catch (DecoderFallbackException)
        {
            return false;
        }
    }

    private static bool LooksLikeUtf16LittleEndian(byte[] bytes)
    {
        if (bytes.Length < 4)
        {
            return false;
        }

        var pairCount = bytes.Length / 2;
        var oddNulls = 0;
        var evenNulls = 0;

        for (var i = 0; i < bytes.Length; i++)
        {
            if (bytes[i] != 0)
            {
                continue;
            }

            if ((i & 1) == 0)
            {
                evenNulls++;
            }
            else
            {
                oddNulls++;
            }
        }

        return oddNulls >= Math.Max(2, pairCount / 3) && evenNulls <= Math.Max(1, pairCount / 16);
    }

    private static bool LooksLikeUtf16BigEndian(byte[] bytes)
    {
        if (bytes.Length < 4)
        {
            return false;
        }

        var pairCount = bytes.Length / 2;
        var oddNulls = 0;
        var evenNulls = 0;

        for (var i = 0; i < bytes.Length; i++)
        {
            if (bytes[i] != 0)
            {
                continue;
            }

            if ((i & 1) == 0)
            {
                evenNulls++;
            }
            else
            {
                oddNulls++;
            }
        }

        return evenNulls >= Math.Max(2, pairCount / 3) && oddNulls <= Math.Max(1, pairCount / 16);
    }

    private static IEnumerable<Encoding> EnumerateLegacyCandidates(OutputEncodingPreference preference)
    {
        if (preference == OutputEncodingPreference.Utf8)
        {
            yield return Utf8NoBom;
            yield break;
        }

        var seen = new HashSet<int>();
        foreach (var codePage in new[] { GetConsoleOutputCodePage(), GetOemCodePage(), GetAnsiCodePage(), Encoding.Default.CodePage })
        {
            if (codePage <= 0 || !seen.Add(codePage))
            {
                continue;
            }

            Encoding? encoding = null;
            try
            {
                encoding = Encoding.GetEncoding(codePage);
            }
            catch (ArgumentException)
            {
                continue;
            }

            if (encoding is not null)
            {
                yield return encoding;
            }
        }
    }

    private static int GetPreambleLength(byte[] bytes, Encoding encoding)
    {
        var preamble = encoding.GetPreamble();
        if (preamble.Length == 0 || bytes.Length < preamble.Length)
        {
            return 0;
        }

        for (var i = 0; i < preamble.Length; i++)
        {
            if (bytes[i] != preamble[i])
            {
                return 0;
            }
        }

        return preamble.Length;
    }

    private static string DescribeEncoding(Encoding encoding)
    {
        return encoding.CodePage switch
        {
            65001 => "utf-8",
            1200 => "utf-16le",
            1201 => "utf-16be",
            _ => $"{encoding.WebName} (cp{encoding.CodePage})"
        };
    }

    [DllImport("kernel32.dll")]
    private static extern uint GetACP();

    [DllImport("kernel32.dll")]
    private static extern uint GetOEMCP();

    [DllImport("kernel32.dll")]
    private static extern uint GetConsoleOutputCP();

    private static int GetAnsiCodePage() => unchecked((int)GetACP());

    private static int GetOemCodePage() => unchecked((int)GetOEMCP());

    private static int GetConsoleOutputCodePage() => unchecked((int)GetConsoleOutputCP());
}

internal sealed record DetectedEncoding(Encoding Encoding, string Label);
