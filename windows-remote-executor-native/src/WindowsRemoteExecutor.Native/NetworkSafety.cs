using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;

namespace WindowsRemoteExecutor.Native;

internal static class NetworkSafety
{
    public static bool IsExpectedPrivateAddress(IPAddress address)
    {
        if (IPAddress.IsLoopback(address))
        {
            return true;
        }

        if (address.AddressFamily == AddressFamily.InterNetworkV6)
        {
            return IsUniqueLocal(address) || address.IsIPv6LinkLocal;
        }

        var bytes = address.GetAddressBytes();
        return bytes[0] switch
        {
            10 => true,
            100 => bytes[1] >= 64 && bytes[1] <= 127,
            127 => true,
            169 => bytes[1] == 254,
            192 => bytes[1] == 168,
            _ => false
        };
    }

    public static string? FindRecommendedListenAddress()
    {
        var candidates = NetworkInterface.GetAllNetworkInterfaces()
            .Where(network => network.OperationalStatus == OperationalStatus.Up)
            .SelectMany(network => network.GetIPProperties().UnicastAddresses)
            .Where(unicast => unicast.Address.AddressFamily == AddressFamily.InterNetwork)
            .Select(unicast => unicast.Address)
            .Where(IsExpectedPrivateAddress)
            .OrderBy(GetPriority)
            .ThenBy(address => address.ToString(), StringComparer.Ordinal)
            .ToArray();

        return candidates.FirstOrDefault()?.ToString();
    }

    public static int GetPriority(IPAddress address)
    {
        var bytes = address.GetAddressBytes();
        if (bytes[0] == 100 && bytes[1] >= 64 && bytes[1] <= 127)
        {
            return 0;
        }

        if (bytes[0] == 10)
        {
            return 1;
        }

        if (bytes[0] == 192 && bytes[1] == 168)
        {
            return 2;
        }

        if (bytes[0] == 127)
        {
            return 3;
        }

        if (bytes[0] == 169 && bytes[1] == 254)
        {
            return 4;
        }

        return 10;
    }

    private static bool IsUniqueLocal(IPAddress address)
    {
        var bytes = address.GetAddressBytes();
        return bytes.Length > 0 && (bytes[0] & 0xfe) == 0xfc;
    }
}
