' tvui relay: while the TV shows the tvui home screen, this runs on the Roku
' and forwards Roku remote presses to the tvui server, which sends them to the TV.
sub Main(args as Dynamic)
    reg = CreateObject("roRegistrySection", "tvui")
    server = ""
    if args <> invalid and args.server <> invalid then server = args.server
    if server = "" then
        server = reg.Read("server")
    else
        reg.Write("server", server)
        reg.Flush()
    end if

    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.setMessagePort(port)
    scene = screen.CreateScene("RelayScene")
    screen.show()
    scene.server = server

    ' A relaunch while running (tvui re-sending the server address) arrives here.
    input = CreateObject("roInput")
    input.SetMessagePort(port)

    while true
        msg = wait(0, port)
        if type(msg) = "roSGScreenEvent" and msg.isScreenClosed() then return
        if type(msg) = "roInputEvent" then
            info = msg.GetInfo()
            if info <> invalid and info.server <> invalid then
                scene.server = info.server
                reg.Write("server", info.server)
                reg.Flush()
            end if
        end if
    end while
end sub

' The tvui screensaver (Settings > Theme > Screensaver > tvui).
sub RunScreenSaver()
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.setMessagePort(port)
    screen.CreateScene("SaverScene")
    screen.show()
    while true
        msg = wait(0, port)
        if type(msg) = "roSGScreenEvent" and msg.isScreenClosed() then return
    end while
end sub
